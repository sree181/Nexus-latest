package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/config"
	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/cursorhook"
	"github.com/sree181/Nexus-latest/recorder/internal/gate"
	"github.com/sree181/Nexus-latest/recorder/internal/ipc"
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
	runtime, err := config.Load()
	if err != nil {
		return err
	}
	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	if command != "status" {
		processLock, err := singleton.Acquire(runtime.Home + string(os.PathSeparator) + "recorder.lock")
		if err != nil {
			return err
		}
		defer processLock.Close()
	}
	key, protection, err := config.LoadOrCreateQueueKey(runtime.QueueKeyPath)
	if err != nil {
		return err
	}
	store, err := cryptoqueue.Open(runtime.DatabasePath, key, cryptoqueue.Limits{MaxBatches: runtime.MaxQueueBatches, MaxBytes: runtime.MaxQueueBytes})
	if err != nil {
		return err
	}
	defer store.Close()

	identity := func() (string, string) { return config.RecordingIdentity(runtime.Home) }
	client := protocol.Client{Origin: runtime.API, DeviceToken: runtime.DeviceToken, LocalUser: runtime.LocalUser, Identity: identity, HTTP: &http.Client{Timeout: time.Duration(runtime.HTTPTimeoutMS) * time.Millisecond}}
	sender := &service.Sender{Store: store, Client: client, Timeout: time.Duration(runtime.HTTPTimeoutMS) * time.Millisecond, Poll: 10 * time.Second, Logger: log.New(os.Stderr, "", log.LstdFlags|log.LUTC)}

	switch command {
	case "status":
		summary, err := store.Summary(context.Background())
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"version": version, "endpoint": runtime.API, "endpoint_source": runtime.EndpointSource, "socket": runtime.SocketPath, "database": runtime.DatabasePath, "queue_key_protection": protection, "credentials": map[string]bool{"device": runtime.DeviceToken != ""}, "queue": summary})
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
			return fmt.Errorf("%d validation-blocked batch(es) remain retained", summary.BlockedBatches)
		}
		return nil
	case "serve":
		return serve(runtime, protection, store, client)
	default:
		return fmt.Errorf("unknown command %q (expected serve, status, or replay)", command)
	}
}

func serve(runtime config.Runtime, protection string, store *cryptoqueue.Store, client protocol.Client) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	wake := make(chan struct{}, 1)
	notify := func() {
		select {
		case wake <- struct{}{}:
		default:
		}
	}
	identity := func() (string, string) { return config.RecordingIdentity(runtime.Home) }
	processor := &cursorhook.Processor{Runtime: runtime, Store: store, Gate: gate.Client{Origin: runtime.API, DeviceToken: runtime.DeviceToken, LocalUser: runtime.LocalUser, Identity: identity, HTTP: &http.Client{Timeout: time.Duration(runtime.GateTimeoutMS) * time.Millisecond}}, Notify: notify}
	sender := &service.Sender{Store: store, Client: client, Wake: wake, Timeout: time.Duration(runtime.HTTPTimeoutMS) * time.Millisecond, Poll: 10 * time.Second, Logger: log.New(os.Stderr, "", log.LstdFlags|log.LUTC)}
	go func() { _ = sender.Run(ctx) }()
	log.Printf("meshagent-recorder version=%s diagnostic=ready endpoint=%s key_protection=%s", version, runtime.API, protection)
	return ipc.Serve(ctx, runtime.SocketPath, processor.Handle)
}
