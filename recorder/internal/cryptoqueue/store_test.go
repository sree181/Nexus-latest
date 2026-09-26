package cryptoqueue

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

func testStore(t *testing.T, limits Limits) *Store {
	t.Helper()
	key := bytes.Repeat([]byte{0x42}, 32)
	store, err := Open(filepath.Join(t.TempDir(), "recorder.db"), key, limits)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}

func opening(t *testing.T, sessionID string) protocol.Envelope {
	t.Helper()
	envelope, err := protocol.NewEnvelope("start", "conv-1", sessionID, "/api/v1/developer/sessions", protocol.SessionStart{ID: sessionID, SourceSessionID: "conv-1", SourceEventID: "evt_0123456789abcdef", Adapter: "cursor", AdapterVersion: "native-1a", Repository: protocol.Repository{ID: "repo-demo-0123456789ab", Name: "demo"}, Task: "secret task", StartedAtMS: 1, Sequence: 1})
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}

func eventEnvelope(t *testing.T, sessionID string, sequence int64, path string) protocol.Envelope {
	t.Helper()
	eventID := fmt.Sprintf("evt_%016x", sequence)
	event := protocol.ActivityEvent{EventID: eventID, SourceEventID: "source-" + path, Sequence: sequence, OccurredAtMS: sequence, Type: "file.changed", Payload: map[string]any{"path": path, "operation": "update", "code": "TOP_SECRET_SOURCE"}}
	envelope, err := protocol.NewEnvelope("events", "conv-1", sessionID, "/api/v1/developer/sessions/"+sessionID+"/events", protocol.ActivityBatch{Events: []protocol.ActivityEvent{event}})
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}

func seedSession(t *testing.T, store *Store) string {
	t.Helper()
	const sessionID = "ses_0123456789abcdef"
	_, err := store.EnsureOpening(context.Background(), SessionSeed{Key: "key-1", NativeSession: "conv-1", Repository: "/repo", Editor: "cursor", SessionID: sessionID}, func(string) (protocol.Envelope, error) { return opening(t, sessionID), nil })
	if err != nil {
		t.Fatal(err)
	}
	return sessionID
}

func TestQueuePayloadIsEncryptedAndAuthenticated(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	sessionID := seedSession(t, store)
	_, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
		return eventEnvelope(t, sessionID, first, "secret.py"), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	rows, err := store.db.Query(`SELECT payload_ciphertext FROM queue_records ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	for rows.Next() {
		var ciphertext []byte
		if err := rows.Scan(&ciphertext); err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(ciphertext, []byte("TOP_SECRET_SOURCE")) || bytes.Contains(ciphertext, []byte("secret.py")) {
			t.Fatal("queue ciphertext exposed plaintext")
		}
	}
	record, err := store.LeaseNext(context.Background(), "key-1", 30*time.Second)
	if err != nil || record == nil {
		t.Fatalf("lease: %v", err)
	}
	if record.Envelope.Kind != "start" {
		t.Fatalf("first kind = %s", record.Envelope.Kind)
	}
	if err := store.Acknowledge(context.Background(), *record, map[string]any{"id": sessionID, "last_acked_sequence": json.Number("1")}); err != nil {
		t.Fatal(err)
	}
	record, err = store.LeaseNext(context.Background(), "key-1", 30*time.Second)
	if err != nil || record == nil {
		t.Fatalf("event lease: %v", err)
	}
	if !bytes.Contains(record.Envelope.Body, []byte("TOP_SECRET_SOURCE")) {
		t.Fatal("decrypted payload missing")
	}
}

func TestFullQueueDoesNotAdvanceSequence(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 2, MaxBytes: 1024 * 1024})
	sessionID := seedSession(t, store)
	_, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) { return eventEnvelope(t, sessionID, first, "two.py"), nil })
	if err != nil {
		t.Fatal(err)
	}
	_, err = store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
		return eventEnvelope(t, sessionID, first, "dropped.py"), nil
	})
	if !errors.Is(err, ErrQueueFull) {
		t.Fatalf("error = %v", err)
	}
	session, err := store.Session(context.Background(), "key-1")
	if err != nil {
		t.Fatal(err)
	}
	if session.NextSequence != 3 || session.QueueRejectedEvents != 1 {
		t.Fatalf("session = %#v", session)
	}

	first, _ := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err := store.Acknowledge(context.Background(), *first, map[string]any{"id": sessionID, "last_acked_sequence": 1}); err != nil {
		t.Fatal(err)
	}
	second, _ := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err := store.Acknowledge(context.Background(), *second, map[string]any{"acknowledged_through": 2}); err != nil {
		t.Fatal(err)
	}
	var observed int64
	_, err = store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
		observed = first
		return eventEnvelope(t, sessionID, first, "recovered.py"), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if observed != 3 {
		t.Fatalf("recovered sequence = %d, want 3", observed)
	}
}

func TestQueueLimitAppliesAcrossSessions(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 2, MaxBytes: 1024 * 1024})
	firstID := seedSession(t, store)
	secondID := "ses_fedcba9876543210"
	if _, err := store.EnsureOpening(context.Background(), SessionSeed{
		Key: "key-2", NativeSession: "conv-2", Repository: "/repo", Editor: "cursor", SessionID: secondID,
	}, func(string) (protocol.Envelope, error) { return opening(t, secondID), nil }); err != nil {
		t.Fatal(err)
	}
	if _, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
		return eventEnvelope(t, firstID, first, "global-limit.go"), nil
	}); !errors.Is(err, ErrQueueFull) {
		t.Fatalf("global queue admission error = %v", err)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if summary.QueuedBatches != 2 || summary.RejectedEvents != 1 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestQueueFullOpeningIsCountedGlobally(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 1, MaxBytes: 1024 * 1024})
	seedSession(t, store)
	secondID := "ses_fedcba9876543210"
	_, err := store.EnsureOpening(context.Background(), SessionSeed{
		Key: "key-2", NativeSession: "conv-2", Repository: "/repo", Editor: "cursor", SessionID: secondID,
	}, func(string) (protocol.Envelope, error) { return opening(t, secondID), nil })
	if !errors.Is(err, ErrQueueFull) {
		t.Fatalf("opening error = %v", err)
	}
	summary, err := store.Summary(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if summary.QueuedBatches != 1 || summary.Sessions != 1 || summary.RejectedEvents != 1 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestExpiredLeaseRecoversBeforeNewerActivity(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	seedSession(t, store)
	base := time.Unix(100, 0)
	store.now = func() time.Time { return base }
	first, err := store.LeaseNext(context.Background(), "key-1", 30*time.Second)
	if err != nil || first == nil {
		t.Fatalf("lease: %v", err)
	}
	store.now = func() time.Time { return base.Add(31 * time.Second) }
	recovered, err := store.LeaseNext(context.Background(), "key-1", 30*time.Second)
	if err != nil || recovered == nil {
		t.Fatalf("recover: %v", err)
	}
	if recovered.ID != first.ID {
		t.Fatalf("recovered id %d, want %d", recovered.ID, first.ID)
	}
}

func TestRetryDelayNeverSkipsTheCausalHead(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	base := time.Unix(100, 0)
	store.now = func() time.Time { return base }
	sessionID := seedSession(t, store)
	for _, path := range []string{"two.go", "three.go"} {
		if _, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
			return eventEnvelope(t, sessionID, first, path), nil
		}); err != nil {
			t.Fatal(err)
		}
	}
	opener, err := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err != nil || opener == nil {
		t.Fatalf("opening lease: %v", err)
	}
	if err := store.Acknowledge(context.Background(), *opener, map[string]any{"id": sessionID, "last_acked_sequence": 1}); err != nil {
		t.Fatal(err)
	}
	head, err := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err != nil || head == nil || head.SequenceStart != 2 {
		t.Fatalf("head = %#v err=%v", head, err)
	}
	if err := store.Release(context.Background(), *head, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	if next, err := store.LeaseNext(context.Background(), "key-1", time.Second); err != nil || next != nil {
		t.Fatalf("newer record bypassed delayed head: next=%#v err=%v", next, err)
	}
	store.now = func() time.Time { return base.Add(11 * time.Second) }
	retry, err := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err != nil || retry == nil || retry.SequenceStart != 2 {
		t.Fatalf("retry = %#v err=%v", retry, err)
	}
}

func TestSessionCloseIsDurableAndIdempotent(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	sessionID := seedSession(t, store)
	buildEnd := func(first int64) (protocol.Envelope, error) {
		event := protocol.ActivityEvent{EventID: fmt.Sprintf("evt_%016x", first), SourceEventID: "source-end", Sequence: first, OccurredAtMS: first, Type: "session.ended", Payload: map[string]any{"reason": "normal"}}
		return protocol.NewEnvelope("events", "conv-1", sessionID, "/api/v1/developer/sessions/"+sessionID+"/events", protocol.ActivityBatch{Events: []protocol.ActivityEvent{event}})
	}
	if _, err := store.CloseWithActivity(context.Background(), "key-1", buildEnd); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CloseWithActivity(context.Background(), "key-1", buildEnd); !errors.Is(err, ErrSessionClosed) {
		t.Fatalf("duplicate close error = %v", err)
	}
	if _, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
		return eventEnvelope(t, sessionID, first, "after-close.go"), nil
	}); !errors.Is(err, ErrSessionClosed) {
		t.Fatalf("post-close error = %v", err)
	}
	session, err := store.Session(context.Background(), "key-1")
	if err != nil || !session.Closed || session.NextSequence != 3 {
		t.Fatalf("session = %#v err=%v", session, err)
	}
}

func TestTamperedCiphertextIsRejected(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 10, MaxBytes: 1024 * 1024})
	seedSession(t, store)
	if _, err := store.db.Exec(`UPDATE queue_records SET payload_ciphertext=zeroblob(length(payload_ciphertext))`); err != nil {
		t.Fatal(err)
	}
	if _, err := store.LeaseNext(context.Background(), "key-1", time.Second); err == nil {
		t.Fatal("expected authentication failure")
	}
}

func TestConcurrentAdmissionReservesUniqueContiguousSequences(t *testing.T) {
	store := testStore(t, Limits{MaxBatches: 100, MaxBytes: 4 * 1024 * 1024})
	sessionID := seedSession(t, store)
	var wait sync.WaitGroup
	errorsFound := make(chan error, 40)
	for index := 0; index < 40; index++ {
		index := index
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, err := store.EnqueueActivity(context.Background(), "key-1", 1, func(first int64) (protocol.Envelope, error) {
				return eventEnvelope(t, sessionID, first, fmt.Sprintf("concurrent-%02d.go", index)), nil
			})
			if err != nil {
				errorsFound <- err
			}
		}()
	}
	wait.Wait()
	close(errorsFound)
	for err := range errorsFound {
		t.Error(err)
	}
	first, err := store.LeaseNext(context.Background(), "key-1", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Acknowledge(context.Background(), *first, map[string]any{"id": sessionID, "last_acked_sequence": 1}); err != nil {
		t.Fatal(err)
	}
	for want := int64(2); want <= 41; want++ {
		record, err := store.LeaseNext(context.Background(), "key-1", time.Second)
		if err != nil || record == nil {
			t.Fatalf("lease sequence %d: %v", want, err)
		}
		if record.SequenceStart != want || record.SequenceEnd != want {
			t.Fatalf("sequence = %d..%d, want %d", record.SequenceStart, record.SequenceEnd, want)
		}
		if err := store.Acknowledge(context.Background(), *record, map[string]any{"acknowledged_through": want}); err != nil {
			t.Fatal(err)
		}
	}
}
