package protocol

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func startFixture(t *testing.T) Envelope {
	t.Helper()
	envelope, err := NewEnvelope("start", "conv-1", "ses_0123456789abcdef", "/api/v1/developer/sessions", SessionStart{
		ID: "ses_0123456789abcdef", SourceSessionID: "conv-1", SourceEventID: "evt_0123456789abcdef",
		Adapter: "cursor", AdapterVersion: "native-1a", Repository: Repository{ID: "repo-demo-0123456789ab", Name: "demo"}, Task: "test", StartedAtMS: 1, Sequence: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}

func TestBatchKeyMatchesPythonCanonicalJSON(t *testing.T) {
	got, err := BatchKey(startFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	const want = "batch_c83b2920a82801299671583adfd59294cd67d7d9971a01610e469246c9fadce3"
	if got != want {
		t.Fatalf("BatchKey = %s, want %s", got, want)
	}
}

func TestClientPostsOnlyBodyWithRecorderHeaders(t *testing.T) {
	envelope := startFixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/developer/sessions" {
			t.Errorf("path = %s", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer mesh_device.secret" {
			t.Errorf("missing device bearer")
		}
		if request.Header.Get("Idempotency-Key") == "" {
			t.Errorf("missing idempotency key")
		}
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		if _, leaked := body["protocol"]; leaked {
			t.Errorf("posted envelope instead of body")
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{"id": envelope.SessionID, "last_acked_sequence": 1})
	}))
	defer server.Close()
	client := Client{Origin: server.URL, DeviceToken: "mesh_device.secret", HTTP: server.Client()}
	if _, err := client.Post(context.Background(), envelope); err != nil {
		t.Fatal(err)
	}
}

func TestClientRejectsPartialAcknowledgement(t *testing.T) {
	event := ActivityEvent{EventID: "evt_0123456789abcdef", SourceEventID: "evt_0123456789abcdef", Sequence: 2, OccurredAtMS: 1, Type: "tool.completed", Payload: map[string]any{"tool_name": "Shell", "detail": "pytest", "exit_code": nil}}
	envelope, _ := NewEnvelope("events", "conv-1", "ses_0123456789abcdef", "/api/v1/developer/sessions/ses_0123456789abcdef/events", ActivityBatch{Events: []ActivityEvent{event}})
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		_ = json.NewEncoder(writer).Encode(map[string]any{"acknowledged_through": 1})
	}))
	defer server.Close()
	client := Client{Origin: server.URL, HTTP: server.Client()}
	if _, err := client.Post(context.Background(), envelope); err == nil {
		t.Fatal("expected partial acknowledgement error")
	}
}

func TestClientReloadsRecordingIdentityForEveryAttempt(t *testing.T) {
	envelope := startFixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer mesh_rotated.secret" {
			t.Errorf("authorization = %q", request.Header.Get("Authorization"))
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{"id": envelope.SessionID, "last_acked_sequence": 1})
	}))
	defer server.Close()
	client := Client{
		Origin: server.URL, DeviceToken: "mesh_stale.secret", HTTP: server.Client(),
		Identity: func() (string, string) { return "mesh_rotated.secret", "" },
	}
	if _, err := client.Post(context.Background(), envelope); err != nil {
		t.Fatal(err)
	}
}

func TestClientRefusesRedirects(t *testing.T) {
	reached := false
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		reached = true
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer target.Close()
	redirector := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		http.Redirect(writer, request, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirector.Close()
	client := Client{Origin: redirector.URL, DeviceToken: "mesh_device.secret", HTTP: redirector.Client()}
	if _, err := client.Post(context.Background(), startFixture(t)); err == nil {
		t.Fatal("expected redirect rejection")
	}
	if reached {
		t.Fatal("redirect target received the recording request")
	}
}
