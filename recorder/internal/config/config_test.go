package config

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func TestValidateEndpoint(t *testing.T) {
	valid := map[string]string{
		" HTTPS://Example.COM/ ": "https://example.com",
		"http://localhost:8000":  "http://localhost:8000",
		"https://[::1]:8443/":    "https://[::1]:8443",
	}
	for input, want := range valid {
		got, err := ValidateEndpoint(input)
		if err != nil || got != want {
			t.Errorf("ValidateEndpoint(%q) = %q, %v; want %q", input, got, err, want)
		}
	}
	for _, input := range []string{"", "ftp://host", "https://user:pass@host", "https://host/api", "https://host?q=1", "https://host/#x", "https://host:99999"} {
		if _, err := ValidateEndpoint(input); err == nil {
			t.Errorf("ValidateEndpoint(%q) succeeded", input)
		}
	}
}

func TestLoadUsesOnlyRecordingDeviceToken(t *testing.T) {
	home := t.TempDir()
	t.Setenv("MESHAGENT_HOOK_HOME", home)
	t.Setenv("MESHAGENT_API", "https://api.example.test/")
	t.Setenv("MESHAGENT_TOKEN", "human-oidc-token")
	runtime, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if runtime.API != "https://api.example.test" || runtime.EndpointSource != "environment" {
		t.Fatalf("runtime = %#v", runtime)
	}
	if runtime.DeviceToken != "" {
		t.Fatal("human token was accepted as recorder credential")
	}
	t.Setenv("MESHAGENT_TOKEN", "mesh_device.secret")
	runtime, err = Load()
	if err != nil {
		t.Fatal(err)
	}
	if runtime.DeviceToken != "mesh_device.secret" {
		t.Fatal("device token was not loaded")
	}
}

func TestLoadRejectsUnknownEnvironmentBeforeCreatingState(t *testing.T) {
	home := filepath.Join(t.TempDir(), "recorder-home")
	t.Setenv("MESHAGENT_HOOK_HOME", home)
	t.Setenv("MESHAGENT_ENV", "prod")
	if _, err := Load(); err == nil {
		t.Fatal("unknown environment was accepted")
	}
	if _, err := os.Stat(home); !os.IsNotExist(err) {
		t.Fatalf("state directory exists after rejected environment: %v", err)
	}
}

func TestQueueKeyFileIsPrivateAndStable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "queue.key")
	first, protection, err := LoadOrCreateQueueKey(path)
	if err != nil {
		t.Fatal(err)
	}
	second, _, err := LoadOrCreateQueueKey(path)
	if err != nil {
		t.Fatal(err)
	}
	if base64.RawURLEncoding.EncodeToString(first) != base64.RawURLEncoding.EncodeToString(second) || protection != "file-development" {
		t.Fatal("queue key was not stable")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("key mode = %v", info.Mode().Perm())
	}
}

func TestQueueKeyLossRequiresExplicitRecovery(t *testing.T) {
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "recorder.db"), []byte("existing queue"), 0o600); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, "recorder.queue-key")
	if _, _, err := LoadOrCreateQueueKey(path); err == nil {
		t.Fatal("missing queue key was silently replaced for an existing database")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("replacement key exists after rejected recovery: %v", err)
	}
}
