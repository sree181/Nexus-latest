package config

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const (
	DefaultAPI             = "http://localhost:8000"
	DefaultMaxQueueBatches = 200
	DefaultMaxQueueBytes   = int64(5 * 1024 * 1024)
	MaxQueueBatchesCeiling = 10000
	MaxQueueBytesCeiling   = int64(100 * 1024 * 1024)
	MaxFrameBytes          = 2 * 1024 * 1024
)

type Runtime struct {
	Home            string
	API             string
	EndpointSource  string
	DeviceToken     string
	LocalUser       string
	DatabasePath    string
	QueueKeyPath    string
	SocketPath      string
	MaxQueueBatches int
	MaxQueueBytes   int64
	HTTPTimeoutMS   int
	GateTimeoutMS   int
	MaxFileBytes    int64
}

type diskConfig struct {
	API string `json:"api"`
}

type credentials struct {
	API   string `json:"api"`
	Token string `json:"token"`
}

func Load() (Runtime, error) {
	home, err := homePath()
	if err != nil {
		return Runtime{}, err
	}
	if err := ensurePrivateDir(home); err != nil {
		return Runtime{}, err
	}

	cfg := readJSON[diskConfig](filepath.Join(home, "config.json"))
	creds := readJSON[credentials](filepath.Join(home, "credentials.json"))
	api, source := endpointFrom(os.Getenv("MESHAGENT_API"), cfg.API, creds.API)
	token, localUser := RecordingIdentity(home)

	return Runtime{
		Home:            home,
		API:             api,
		EndpointSource:  source,
		DeviceToken:     token,
		LocalUser:       localUser,
		DatabasePath:    filepath.Join(home, "recorder.db"),
		QueueKeyPath:    filepath.Join(home, "recorder.queue-key"),
		SocketPath:      socketPath(home),
		MaxQueueBatches: envInt("MESHAGENT_QUEUE_MAX_BATCHES", DefaultMaxQueueBatches, MaxQueueBatchesCeiling),
		MaxQueueBytes:   envInt64("MESHAGENT_QUEUE_MAX_BYTES", DefaultMaxQueueBytes, MaxQueueBytesCeiling),
		HTTPTimeoutMS:   envInt("MESHAGENT_HOOK_TIMEOUT_MS", 2000, 30000),
		GateTimeoutMS:   envInt("MESHAGENT_GATE_TIMEOUT_MS", 4500, 5500),
		MaxFileBytes:    envInt64("MESHAGENT_HOOK_MAX_BYTES", 200000, 200000),
	}, nil
}

func RecordingIdentity(home string) (string, string) {
	token := strings.TrimSpace(os.Getenv("MESHAGENT_TOKEN"))
	if token == "" {
		token = strings.TrimSpace(readJSON[credentials](filepath.Join(home, "credentials.json")).Token)
	}
	if !strings.HasPrefix(token, "mesh_") || !strings.Contains(token, ".") {
		token = ""
	}
	return token, strings.TrimSpace(os.Getenv("MESHAGENT_USER"))
}

func ValidateEndpoint(raw string) (string, error) {
	candidate := strings.TrimSuffix(strings.TrimSpace(raw), "/")
	if candidate == "" {
		return "", errors.New("API endpoint is required")
	}
	parsed, err := url.Parse(candidate)
	if err != nil {
		return "", fmt.Errorf("parse API endpoint: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return "", errors.New("API endpoint must start with http:// or https://")
	}
	if parsed.Hostname() == "" || parsed.User != nil {
		return "", errors.New("API endpoint must be an origin without credentials")
	}
	if (parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", errors.New("API endpoint must not include a path, query, or fragment")
	}
	if parsed.Port() != "" {
		port, err := strconv.Atoi(parsed.Port())
		if err != nil || port < 1 || port > 65535 {
			return "", errors.New("API endpoint has an invalid port")
		}
	}
	host := strings.ToLower(parsed.Hostname())
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	if parsed.Port() != "" {
		host = net.JoinHostPort(strings.Trim(host, "[]"), parsed.Port())
	}
	return strings.ToLower(parsed.Scheme) + "://" + host, nil
}

func endpointFrom(envValue, configValue, credentialValue string) (string, string) {
	if strings.TrimSpace(envValue) != "" {
		if valid, err := ValidateEndpoint(envValue); err == nil {
			return valid, "environment"
		}
	}
	if strings.TrimSpace(configValue) != "" {
		if valid, err := ValidateEndpoint(configValue); err == nil {
			return valid, "config"
		}
	}
	if strings.TrimSpace(credentialValue) != "" {
		if valid, err := ValidateEndpoint(credentialValue); err == nil {
			return valid, "legacy credentials"
		}
	}
	return DefaultAPI, "default"
}

func LoadOrCreateQueueKey(path string) ([]byte, string, error) {
	if override := strings.TrimSpace(os.Getenv("MESHAGENT_QUEUE_KEY")); override != "" {
		decoded, err := base64.RawURLEncoding.DecodeString(override)
		if err != nil || len(decoded) != 32 {
			return nil, "", errors.New("MESHAGENT_QUEUE_KEY must be unpadded base64url for exactly 32 bytes")
		}
		return decoded, "environment-development", nil
	}
	if encoded, err := os.ReadFile(path); err == nil {
		decoded, decodeErr := base64.RawURLEncoding.DecodeString(strings.TrimSpace(string(encoded)))
		if decodeErr != nil || len(decoded) != 32 {
			return nil, "", errors.New("existing recorder queue key is invalid")
		}
		if err := chmodPrivate(path, 0o600); err != nil {
			return nil, "", err
		}
		return decoded, "file-development", nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, "", err
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(path), "recorder.db")); err == nil {
		return nil, "", errors.New("queue key is missing for an existing recorder database; recovery or authorized queue reset is required")
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, "", err
	}

	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, "", fmt.Errorf("generate queue key: %w", err)
	}
	encoded := base64.RawURLEncoding.EncodeToString(key) + "\n"
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return nil, "", fmt.Errorf("create queue key: %w", err)
	}
	if _, err := file.WriteString(encoded); err != nil {
		_ = file.Close()
		return nil, "", fmt.Errorf("write queue key: %w", err)
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return nil, "", fmt.Errorf("sync queue key: %w", err)
	}
	if err := file.Close(); err != nil {
		return nil, "", err
	}
	if err := chmodPrivate(path, 0o600); err != nil {
		return nil, "", err
	}
	return key, "file-development", nil
}

func homePath() (string, error) {
	if configured := strings.TrimSpace(os.Getenv("MESHAGENT_HOOK_HOME")); configured != "" {
		return filepath.Abs(configured)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".meshagent"), nil
}

func ensurePrivateDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	return chmodPrivate(path, 0o700)
}

func readJSON[T any](path string) T {
	var value T
	body, err := os.ReadFile(path)
	if err != nil {
		return value
	}
	if err := json.Unmarshal(body, &value); err != nil {
		var zero T
		return zero
	}
	return value
}

func envInt(name string, fallback, ceiling int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value < 1 {
		return fallback
	}
	if value > ceiling {
		return ceiling
	}
	return value
}

func envInt64(name string, fallback, ceiling int64) int64 {
	value, err := strconv.ParseInt(strings.TrimSpace(os.Getenv(name)), 10, 64)
	if err != nil || value < 1 {
		return fallback
	}
	if value > ceiling {
		return ceiling
	}
	return value
}
