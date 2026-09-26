//go:build !windows

package ipc

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestProtectedLocalRoundTrip(t *testing.T) {
	address := filepath.Join(t.TempDir(), "run", "recorder.sock")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	ready := make(chan struct{})
	go func() {
		close(ready)
		_ = Serve(ctx, address, func(ctx context.Context, request Request) Response {
			return Response{Accepted: true, CursorResponse: &PermissionResponse{Permission: "allow"}}
		})
	}()
	<-ready
	var response Response
	var err error
	for attempt := 0; attempt < 50; attempt++ {
		response, err = Send(context.Background(), address, Request{Protocol: Protocol, RequestID: "req-1", DeadlineMS: 1000, Editor: "cursor", Event: map[string]any{"hook_event_name": "beforeShellExecution"}})
		if err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	if !response.Accepted || response.CursorResponse == nil || response.CursorResponse.Permission != "allow" {
		t.Fatalf("response = %#v", response)
	}
}

func TestListenRefusesNonSocketAddress(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "run")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	address := filepath.Join(directory, "recorder.sock")
	if err := os.WriteFile(address, []byte("preserve"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := listen(address); err == nil {
		t.Fatal("listener replaced a non-socket path")
	}
	body, err := os.ReadFile(address)
	if err != nil || string(body) != "preserve" {
		t.Fatalf("existing path changed: body=%q err=%v", body, err)
	}
}
