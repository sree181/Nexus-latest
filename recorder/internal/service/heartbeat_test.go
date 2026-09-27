package service

import (
	"bytes"
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
)

type heartbeatClient struct {
	fetched int
	applied int
	payload map[string]any
}

func (c *heartbeatClient) RefreshConfiguration(context.Context) error {
	c.fetched++
	return nil
}

func (c *heartbeatClient) ConfigVersion() int { return c.applied }

func (c *heartbeatClient) PostHeartbeat(_ context.Context, payload any) error {
	c.payload = payload.(map[string]any)
	return nil
}

func TestHeartbeatReportsAppliedNotMerelyFetchedConfiguration(t *testing.T) {
	store, err := cryptoqueue.Open(
		filepath.Join(t.TempDir(), "queue.sqlite3"),
		bytes.Repeat([]byte{1}, 32),
		cryptoqueue.Limits{MaxBatches: 10, MaxBytes: 1024 * 1024},
	)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	client := &heartbeatClient{applied: 1}
	heartbeat := &Heartbeater{
		Store: store, Client: client, RecorderVersion: "test",
		PlatformVersion: "test", Now: func() time.Time { return time.Unix(100, 0) },
	}
	heartbeat.send(context.Background())
	if client.fetched != 1 {
		t.Fatalf("configuration refresh calls = %d", client.fetched)
	}
	if got := client.payload["config_version"]; got != 1 {
		t.Fatalf("heartbeat overstated applied configuration: %v", got)
	}
	client.applied = 2
	heartbeat.send(context.Background())
	if got := client.payload["config_version"]; got != 2 {
		t.Fatalf("heartbeat did not report activated configuration: %v", got)
	}
}
