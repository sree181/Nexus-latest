package service

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

type fakePoster struct{ seen []protocol.Envelope }

func (p *fakePoster) Post(_ context.Context, envelope protocol.Envelope) (map[string]any, error) {
	p.seen = append(p.seen, envelope)
	if envelope.Kind == "start" {
		return map[string]any{"id": envelope.SessionID, "last_acked_sequence": 1}, nil
	}
	sequence, _ := envelope.FinalSequence()
	return map[string]any{"acknowledged_through": sequence}, nil
}

type validationPoster struct{ calls int }

func (p *validationPoster) Post(context.Context, protocol.Envelope) (map[string]any, error) {
	p.calls++
	return nil, &protocol.HTTPError{StatusCode: 422}
}

func TestDrainOnceSendsOpeningBeforeActivity(t *testing.T) {
	store, err := cryptoqueue.Open(filepath.Join(t.TempDir(), "recorder.db"), make([]byte, 32), cryptoqueue.Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	const sessionID = "ses_0123456789abcdef"
	_, err = store.EnsureOpening(context.Background(), cryptoqueue.SessionSeed{Key: "key", NativeSession: "conv", Repository: "/repo", Editor: "cursor", SessionID: sessionID}, func(string) (protocol.Envelope, error) {
		return protocol.NewEnvelope("start", "conv", sessionID, "/api/v1/developer/sessions", protocol.SessionStart{ID: sessionID, SourceSessionID: "conv", SourceEventID: "evt_0123456789abcdef", Adapter: "cursor", AdapterVersion: "native-1a", Repository: protocol.Repository{ID: "repo-demo-0123456789ab", Name: "demo"}, Task: "task", StartedAtMS: 1, Sequence: 1})
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = store.EnqueueActivity(context.Background(), "key", 1, func(first int64) (protocol.Envelope, error) {
		return protocol.NewEnvelope("events", "conv", sessionID, "/api/v1/developer/sessions/"+sessionID+"/events", protocol.ActivityBatch{Events: []protocol.ActivityEvent{{EventID: "evt_fedcba9876543210", SourceEventID: "evt_fedcba9876543210", Sequence: first, OccurredAtMS: 2, Type: "tool.completed", Payload: map[string]any{"tool_name": "Shell", "detail": "pytest", "exit_code": nil}}}})
	})
	if err != nil {
		t.Fatal(err)
	}
	poster := &fakePoster{}
	sender := &Sender{Store: store, Client: poster, Timeout: time.Second}
	if err := sender.DrainOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(poster.seen) != 2 || poster.seen[0].Kind != "start" || poster.seen[1].Kind != "events" {
		t.Fatalf("seen = %#v", poster.seen)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if summary.QueuedBatches != 0 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestPermanentValidationFailureIsRetainedWithoutHotRetry(t *testing.T) {
	store, err := cryptoqueue.Open(filepath.Join(t.TempDir(), "recorder.db"), make([]byte, 32), cryptoqueue.Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	const sessionID = "ses_0123456789abcdef"
	_, err = store.EnsureOpening(context.Background(), cryptoqueue.SessionSeed{Key: "key", NativeSession: "conv", Repository: "/repo", Editor: "cursor", SessionID: sessionID}, func(string) (protocol.Envelope, error) {
		return protocol.NewEnvelope("start", "conv", sessionID, "/api/v1/developer/sessions", protocol.SessionStart{ID: sessionID, SourceSessionID: "conv", SourceEventID: "evt_0123456789abcdef", Adapter: "cursor", AdapterVersion: "native-1a", Repository: protocol.Repository{ID: "repo-demo-0123456789ab", Name: "demo"}, Task: "task", StartedAtMS: 1, Sequence: 1})
	})
	if err != nil {
		t.Fatal(err)
	}
	poster := &validationPoster{}
	sender := &Sender{Store: store, Client: poster, Timeout: time.Second}
	if err := sender.DrainOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := sender.DrainOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if poster.calls != 1 || summary.QueuedBatches != 1 || summary.BlockedBatches != 1 {
		t.Fatalf("calls=%d summary=%#v", poster.calls, summary)
	}
	if err := store.UnblockAll(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := sender.DrainOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if poster.calls != 2 {
		t.Fatalf("manual recovery did not retry retained batch: calls=%d", poster.calls)
	}
}
