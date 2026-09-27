package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/config"
	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/cursorhook"
	"github.com/sree181/Nexus-latest/recorder/internal/enterprise"
	"github.com/sree181/Nexus-latest/recorder/internal/gate"
	"github.com/sree181/Nexus-latest/recorder/internal/ipc"
	"github.com/sree181/Nexus-latest/recorder/internal/openbrowser"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
	"github.com/sree181/Nexus-latest/recorder/internal/service"
	"github.com/sree181/Nexus-latest/recorder/internal/singleton"
)

var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "meshagent-recorder:", err)
		os.Exit(1)
	}
}

func run() error {
	config.ApplyPrivateUmask()
	runtimeConfig, err := config.Load()
	if err != nil {
		return err
	}
	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	if command == "enroll" {
		processLock, err := singleton.Acquire(runtimeConfig.Home + string(os.PathSeparator) + "recorder.lock")
		if err != nil {
			return errors.New("stop the recorder service before enterprise enrollment")
		}
		defer processLock.Close()
		return enroll(runtimeConfig, os.Args[2:])
	}
	if command != "status" {
		processLock, err := singleton.Acquire(runtimeConfig.Home + string(os.PathSeparator) + "recorder.lock")
		if err != nil {
			return err
		}
		defer processLock.Close()
	}

	var manager *enterprise.Manager
	var startupConfig *enterprise.SignedConfig
	var key []byte
	var protection string
	if runtimeConfig.EnterpriseMode {
		manager, err = enterprise.LoadDevelopment(runtimeConfig.API, runtimeConfig.Home, nil)
		if err != nil {
			return err
		}
		if !manager.Enrolled() {
			return errors.New("enterprise mode requires completed recorder enrollment")
		}
		if command != "status" {
			signed, err := manager.LoadConfig(context.Background())
			if err != nil {
				return fmt.Errorf("load signed enterprise configuration: %w", err)
			}
			if err := applySignedLimits(&runtimeConfig, signed); err != nil {
				return err
			}
			startupConfig = &signed
		}
		key, protection, err = enterprise.LoadOrCreateQueueKey(runtimeConfig.Home, runtimeConfig.DatabasePath, manager.KeyStore)
	} else {
		key, protection, err = config.LoadOrCreateQueueKey(runtimeConfig.QueueKeyPath)
	}
	if err != nil {
		return err
	}
	store, err := cryptoqueue.Open(runtimeConfig.DatabasePath, key, cryptoqueue.Limits{MaxBatches: runtimeConfig.MaxQueueBatches, MaxBytes: runtimeConfig.MaxQueueBytes})
	if err != nil {
		return err
	}
	defer store.Close()
	if manager != nil && startupConfig != nil {
		if err := manager.MarkConfigApplied(*startupConfig); err != nil {
			return fmt.Errorf("record applied enterprise configuration: %w", err)
		}
	}

	identity := func() (string, string) { return config.RecordingIdentity(runtimeConfig.Home) }
	client := protocol.Client{Origin: runtimeConfig.API, DeviceToken: runtimeConfig.DeviceToken, LocalUser: runtimeConfig.LocalUser, Identity: identity, HTTP: &http.Client{Timeout: time.Duration(runtimeConfig.HTTPTimeoutMS) * time.Millisecond}}
	if manager != nil {
		client.Authorizer = manager
	}
	sender := &service.Sender{Store: store, Client: client, Timeout: time.Duration(runtimeConfig.HTTPTimeoutMS) * time.Millisecond, Poll: 10 * time.Second, Logger: log.New(os.Stderr, "", log.LstdFlags|log.LUTC)}

	switch command {
	case "status":
		summary, err := store.Summary(context.Background())
		if err != nil {
			return err
		}
		mode := "legacy"
		credentials := map[string]bool{"device": runtimeConfig.DeviceToken != ""}
		output := map[string]any{"version": version, "endpoint": runtimeConfig.API, "endpoint_source": runtimeConfig.EndpointSource, "socket": runtimeConfig.SocketPath, "database": runtimeConfig.DatabasePath, "queue_key_protection": protection, "credential_mode": mode, "credentials": credentials, "queue": summary}
		if manager != nil {
			output["credential_mode"] = "enterprise-dpop"
			output["key_protection"] = manager.KeyProtection()
			output["config_version"] = manager.ConfigVersion()
			output["credentials"] = map[string]bool{"device": false, "enterprise": true}
		}
		return json.NewEncoder(os.Stdout).Encode(output)
	case "replay":
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := store.UnblockAll(ctx); err != nil {
			return err
		}
		if err := sender.DrainOnce(ctx); err != nil {
			return err
		}
		summary, err := store.Summary(ctx)
		if err != nil {
			return err
		}
		if err := json.NewEncoder(os.Stdout).Encode(summary); err != nil {
			return err
		}
		if summary.BlockedBatches > 0 {
			return fmt.Errorf("%d trust- or validation-blocked batch(es) remain retained", summary.BlockedBatches)
		}
		return nil
	case "serve":
		return serve(runtimeConfig, protection, store, client, manager)
	default:
		return fmt.Errorf("unknown command %q (expected serve, status, replay, or enroll)", command)
	}
}

func serve(runtimeConfig config.Runtime, protection string, store *cryptoqueue.Store, client protocol.Client, manager *enterprise.Manager) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	wake := make(chan struct{}, 1)
	notify := func() {
		select {
		case wake <- struct{}{}:
		default:
		}
	}
	identity := func() (string, string) { return config.RecordingIdentity(runtimeConfig.Home) }
	gateClient := gate.Client{Origin: runtimeConfig.API, DeviceToken: runtimeConfig.DeviceToken, LocalUser: runtimeConfig.LocalUser, Identity: identity, HTTP: &http.Client{Timeout: time.Duration(runtimeConfig.GateTimeoutMS) * time.Millisecond}}
	if manager != nil {
		gateClient.Authorizer = manager
	}
	processor := &cursorhook.Processor{Runtime: runtimeConfig, Store: store, Gate: gateClient, Notify: notify}
	logger := log.New(os.Stderr, "", log.LstdFlags|log.LUTC)
	sender := &service.Sender{Store: store, Client: client, Wake: wake, Timeout: time.Duration(runtimeConfig.HTTPTimeoutMS) * time.Millisecond, Poll: 10 * time.Second, Logger: logger}
	go func() { _ = sender.Run(ctx) }()
	if manager != nil {
		heartbeat := &service.Heartbeater{Store: store, Client: manager, RecorderVersion: version, PlatformVersion: platformVersion(), Interval: time.Duration(runtimeConfig.HeartbeatIntervalSeconds) * time.Second, Logger: logger}
		go func() { _ = heartbeat.Run(ctx) }()
	}
	log.Printf("meshagent-recorder version=%s diagnostic=ready endpoint=%s key_protection=%s", version, runtimeConfig.API, protection)
	return ipc.Serve(ctx, runtimeConfig.SocketPath, processor.Handle)
}

func enroll(runtimeConfig config.Runtime, args []string) error {
	flags := flag.NewFlagSet("enroll", flag.ContinueOnError)
	label := flags.String("label", "", "human-readable managed device label")
	deployment := flags.String("deployment", "", "enterprise deployment identifier")
	wait := flags.Duration("wait", 10*time.Minute, "maximum time to wait for browser approval")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*label) == "" || strings.TrimSpace(*deployment) == "" {
		return errors.New("enroll requires --label and --deployment")
	}
	manager, err := enterprise.LoadDevelopment(runtimeConfig.API, runtimeConfig.Home, nil)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), *wait)
	defer cancel()
	started, err := manager.BeginEnrollment(ctx, enterprise.DefaultEnrollment(*label, *deployment, version))
	if err != nil {
		return err
	}
	browserErr := openbrowser.Open(started.VerifyURL)
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "waiting_for_corporate_approval", "browser_opened": browserErr == nil, "user_code": started.UserCode, "verify_url": started.VerifyURL, "expires_in": started.ExpiresIn, "key_protection": manager.KeyProtection()})
	interval := time.Duration(started.Interval) * time.Second
	if interval < 2*time.Second {
		interval = 2 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		if _, err := manager.Claim(ctx, started.DeviceCode); err == nil {
			if _, err := manager.FetchConfig(ctx); err != nil {
				return err
			}
			_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "enrolled", "key_protection": manager.KeyProtection()})
			return nil
		} else if !enterprise.EnrollmentPollRetryable(err) {
			return fmt.Errorf("enterprise enrollment claim failed: %w", err)
		}
		select {
		case <-ctx.Done():
			return errors.New("enterprise enrollment approval timed out")
		case <-ticker.C:
		}
	}
}

func applySignedLimits(runtimeConfig *config.Runtime, signed enterprise.SignedConfig) error {
	batches, ok := signed.Payload["max_queue_batches"].(float64)
	if !ok || batches < 1 || batches > 10000 {
		return errors.New("signed configuration has invalid queue batch limit")
	}
	bytes, ok := signed.Payload["max_queue_bytes"].(float64)
	if !ok || bytes < 1024 || bytes > 104857600 {
		return errors.New("signed configuration has invalid queue byte limit")
	}
	heartbeat, ok := signed.Payload["heartbeat_interval_seconds"].(float64)
	if !ok || heartbeat < 30 || heartbeat > 3600 {
		return errors.New("signed configuration has invalid heartbeat interval")
	}
	runtimeConfig.MaxQueueBatches = int(batches)
	runtimeConfig.MaxQueueBytes = int64(bytes)
	runtimeConfig.HeartbeatIntervalSeconds = int(heartbeat)
	return nil
}

func platformVersion() string {
	if value := strings.TrimSpace(os.Getenv("MESHAGENT_PLATFORM_VERSION")); value != "" {
		return value
	}
	return "unknown"
}
