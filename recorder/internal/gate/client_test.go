package gate

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClientReloadsIdentityAndDecodesDecision(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/gate/package" {
			t.Errorf("path = %s", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer mesh_rotated.secret" {
			t.Errorf("authorization = %q", request.Header.Get("Authorization"))
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(Decision{
			Package: "requests", Version: "2.19.0", Ecosystem: "PyPI", Verdict: "block", Policy: "test",
		})
	}))
	defer server.Close()
	client := Client{
		Origin: server.URL, DeviceToken: "mesh_stale.secret", HTTP: server.Client(),
		Identity: func() (string, string) { return "mesh_rotated.secret", "" },
	}
	decision, err := client.Ask(context.Background(), Request{Package: "requests", Version: "2.19.0", Ecosystem: "PyPI"})
	if err != nil {
		t.Fatal(err)
	}
	if decision.Verdict != "block" {
		t.Fatalf("decision = %#v", decision)
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
	if _, err := client.Ask(context.Background(), Request{Package: "demo", Version: "1.0.0", Ecosystem: "PyPI"}); err == nil {
		t.Fatal("expected redirect rejection")
	}
	if reached {
		t.Fatal("redirect target received the package-gate request")
	}
}

func TestClientRejectsMalformedBlockResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"verdict":"block"}`))
	}))
	defer server.Close()
	client := Client{Origin: server.URL, HTTP: server.Client()}
	if decision, err := client.Ask(context.Background(), Request{Package: "demo", Version: "1.0.0", Ecosystem: "PyPI"}); err == nil || decision != nil {
		t.Fatalf("malformed block response was accepted: decision=%#v err=%v", decision, err)
	}
}
