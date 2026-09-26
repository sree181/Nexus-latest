package cursorhook

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/config"
	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/gate"
	"github.com/sree181/Nexus-latest/recorder/internal/ipc"
)

type fakeGate struct {
	decision *gate.Decision
	err      error
	requests []gate.Request
}

func (f *fakeGate) Ask(_ context.Context, request gate.Request) (*gate.Decision, error) {
	f.requests = append(f.requests, request)
	return f.decision, f.err
}

func testProcessor(t *testing.T, gateClient GateClient) (*Processor, *cryptoqueue.Store, string) {
	t.Helper()
	repo := t.TempDir()
	if err := os.WriteFile(filepath.Join(repo, ".meshagent.json"), []byte(`{"record":true,"exclude":["secrets/*","*.pem"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := cryptoqueue.Open(filepath.Join(t.TempDir(), "recorder.db"), make([]byte, 32), cryptoqueue.Limits{MaxBatches: 20, MaxBytes: 1024 * 1024})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	processor := &Processor{Runtime: config.Runtime{MaxFileBytes: 200000}, Store: store, Gate: gateClient, Now: func() time.Time { return time.UnixMilli(1000) }}
	return processor, store, repo
}

func request(event map[string]any) ipc.Request {
	return ipc.Request{Protocol: ipc.Protocol, RequestID: "req", DeadlineMS: 1000, Editor: "cursor", Event: event}
}

func TestFirstPromptQueuesStableSessionOpening(t *testing.T) {
	processor, store, repo := testProcessor(t, nil)
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeSubmitPrompt", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "prompt": " Write a shard loader. "}))
	if !response.Accepted {
		t.Fatalf("response = %#v", response)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if summary.QueuedBatches != 1 || summary.Sessions != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	session, err := store.Session(context.Background(), sessionKey(repo, "conv-1"))
	if err != nil {
		t.Fatal(err)
	}
	if !session.Opened || session.OpeningAcknowledged {
		t.Fatalf("session = %#v", session)
	}
}

func TestSuccessfulPinnedInstallCreatesToolAndPackageEvidence(t *testing.T) {
	processor, store, repo := testProcessor(t, nil)
	processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeSubmitPrompt", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "prompt": "Install dependency"}))
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "afterShellExecution", "conversation_id": "conv-1", "cwd": repo, "command": "pip install numpy==1.26.4", "exit_code": json.Number("0")}))
	if !response.Accepted {
		t.Fatalf("response = %#v", response)
	}
	first, _ := store.LeaseNext(context.Background(), sessionKey(repo, "conv-1"), time.Second)
	if err := store.Acknowledge(context.Background(), *first, map[string]any{"id": first.Envelope.SessionID, "last_acked_sequence": 1}); err != nil {
		t.Fatal(err)
	}
	second, err := store.LeaseNext(context.Background(), sessionKey(repo, "conv-1"), time.Second)
	if err != nil || second == nil {
		t.Fatalf("lease: %v", err)
	}
	var body struct {
		Events []struct {
			Sequence int64          `json:"sequence"`
			Type     string         `json:"type"`
			Payload  map[string]any `json:"payload"`
		} `json:"events"`
	}
	if err := json.Unmarshal(second.Envelope.Body, &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Events) != 2 || body.Events[0].Type != "tool.completed" || body.Events[1].Type != "package.installed" || body.Events[0].Sequence != 2 || body.Events[1].Sequence != 3 {
		t.Fatalf("events = %#v", body.Events)
	}
}

func TestPermissionGateFailsOpenOnError(t *testing.T) {
	gateClient := &fakeGate{err: errors.New("offline")}
	processor, _, repo := testProcessor(t, gateClient)
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeShellExecution", "conversation_id": "conv-1", "cwd": repo, "command": "pip install requests==2.19.0"}))
	if response.CursorResponse == nil || response.CursorResponse.Permission != "allow" {
		t.Fatalf("response = %#v", response)
	}
}

func TestOnlyExplicitBlockDeniesAndQueuesPolicyForOpenedSession(t *testing.T) {
	gateClient := &fakeGate{decision: &gate.Decision{Verdict: "block", Reasons: []string{"CVE-2018-18074 (high)"}, Policy: "high and above refused"}}
	processor, store, repo := testProcessor(t, gateClient)
	processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeSubmitPrompt", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "prompt": "Install dependency"}))
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeShellExecution", "conversation_id": "conv-1", "cwd": repo, "command": "pip install requests==2.19.0"}))
	if response.CursorResponse == nil || response.CursorResponse.Permission != "deny" {
		t.Fatalf("response = %#v", response)
	}
	if len(gateClient.requests) != 1 || gateClient.requests[0].Package != "requests" {
		t.Fatalf("requests = %#v", gateClient.requests)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if summary.QueuedBatches != 2 {
		t.Fatalf("queued batches = %d, want opener + policy", summary.QueuedBatches)
	}
}

func TestFileOutsideRepositoryAndExcludedFileAreNotRead(t *testing.T) {
	processor, store, repo := testProcessor(t, nil)
	secretDir := filepath.Join(repo, "secrets")
	_ = os.Mkdir(secretDir, 0o700)
	secret := filepath.Join(secretDir, "key.py")
	_ = os.WriteFile(secret, []byte("TOKEN='x'"), 0o600)
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "afterFileEdit", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "file_path": secret}))
	if response.Accepted || response.DiagnosticCode != "no_observation" {
		t.Fatalf("response = %#v", response)
	}
	summary, _ := store.Summary(context.Background())
	if summary.QueuedBatches != 0 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestSymlinkOutsideRepositoryIsNotRead(t *testing.T) {
	processor, store, repo := testProcessor(t, nil)
	outside := filepath.Join(t.TempDir(), "outside.go")
	if err := os.WriteFile(outside, []byte("EXTERNAL_SECRET = true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(repo, "linked.go")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	response := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "afterFileEdit", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "file_path": link}))
	if response.Accepted || response.DiagnosticCode != "no_observation" {
		t.Fatalf("response = %#v", response)
	}
	summary, _ := store.Summary(context.Background())
	if summary.QueuedBatches != 0 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestSessionEndIsIdempotentAndRejectsLaterActivity(t *testing.T) {
	processor, store, repo := testProcessor(t, nil)
	opening := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "beforeSubmitPrompt", "conversation_id": "conv-1", "workspace_roots": []any{repo}, "prompt": "Close cleanly"}))
	if !opening.Accepted {
		t.Fatalf("opening = %#v", opening)
	}
	ended := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "sessionEnd", "conversation_id": "conv-1", "cwd": repo}))
	if !ended.Accepted {
		t.Fatalf("ended = %#v", ended)
	}
	duplicate := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "sessionEnd", "conversation_id": "conv-1", "cwd": repo}))
	if duplicate.Accepted || duplicate.DiagnosticCode != "session_closed" {
		t.Fatalf("duplicate = %#v", duplicate)
	}
	file := filepath.Join(repo, "after.go")
	if err := os.WriteFile(file, []byte("package after\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	after := processor.Handle(context.Background(), request(map[string]any{"hook_event_name": "afterFileEdit", "conversation_id": "conv-1", "cwd": repo, "file_path": file}))
	if after.Accepted || after.DiagnosticCode != "session_closed" {
		t.Fatalf("after = %#v", after)
	}
	session, err := store.Session(context.Background(), sessionKey(repo, "conv-1"))
	if err != nil || !session.Closed || session.NextSequence != 3 {
		t.Fatalf("session = %#v err=%v", session, err)
	}
}
