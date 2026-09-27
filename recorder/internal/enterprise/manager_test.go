package enterprise

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/keystore"
)

func TestDPoPProofBindsMethodTargetAndToken(t *testing.T) {
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(t.TempDir(), "key.pem"))
	if err != nil {
		t.Fatal(err)
	}
	made, err := proof(store.Signer(), store.PublicJWK(), "POST", "https://mesh.example/api", "token", time.Unix(1000, 0))
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(made, ".")
	if len(parts) != 3 {
		t.Fatal("proof is not compact JWS")
	}
	var claims map[string]any
	if err := decodeJSONPart(parts[1], &claims); err != nil {
		t.Fatal(err)
	}
	if claims["htm"] != "POST" || claims["htu"] != "https://mesh.example/api" {
		t.Fatalf("claims = %#v", claims)
	}
	digest := sha256.Sum256([]byte("token"))
	if claims["ath"] != base64.RawURLEncoding.EncodeToString(digest[:]) {
		t.Fatal("access token hash mismatch")
	}
}

func TestEnrollmentPollingRetriesPendingButNotConsumedOrInvalidClaims(t *testing.T) {
	if !EnrollmentPollRetryable(HTTPStatusError{Status: http.StatusConflict}) {
		t.Fatal("pending enrollment must be retried")
	}
	if EnrollmentPollRetryable(HTTPStatusError{Status: http.StatusUnauthorized}) {
		t.Fatal("invalid or consumed claim must fail immediately")
	}
}

func TestDevelopmentKeyStoreCannotBeSelectedInProduction(t *testing.T) {
	home := t.TempDir()
	t.Setenv("MESHAGENT_ENV", "production")
	t.Setenv("MESHAGENT_ALLOW_SOFTWARE_ENTERPRISE_KEY", "1")
	if _, err := LoadDevelopment("https://mesh.example", home, nil); err == nil {
		t.Fatal("production accepted a development software key")
	}
	if _, err := os.Stat(filepath.Join(home, "enterprise-device-key.pem")); !os.IsNotExist(err) {
		t.Fatalf("development key was created in production: %v", err)
	}
}

func TestDevelopmentKeyStoreRejectsUnknownEnvironment(t *testing.T) {
	home := t.TempDir()
	t.Setenv("MESHAGENT_ENV", "prod")
	if _, err := LoadDevelopment("https://mesh.example", home, nil); err == nil {
		t.Fatal("unknown environment accepted a development software key")
	}
	if _, err := os.Stat(filepath.Join(home, "enterprise-device-key.pem")); !os.IsNotExist(err) {
		t.Fatalf("development key was created for an unknown environment: %v", err)
	}
}

func TestWrappedQueueKeyIsStableAndNotPlaintext(t *testing.T) {
	home := t.TempDir()
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "key.pem"))
	if err != nil {
		t.Fatal(err)
	}
	first, mode, err := LoadOrCreateQueueKey(home, filepath.Join(home, "recorder.db"), store)
	if err != nil {
		t.Fatal(err)
	}
	second, _, err := LoadOrCreateQueueKey(home, filepath.Join(home, "recorder.db"), store)
	if err != nil {
		t.Fatal(err)
	}
	if mode != "software-development" || string(first) != string(second) {
		t.Fatal("wrapped key was not stable or clearly labelled")
	}
	body, _ := os.ReadFile(filepath.Join(home, "recorder.queue-key.wrapped"))
	if strings.Contains(string(body), base64.RawURLEncoding.EncodeToString(first)) {
		t.Fatal("plaintext queue key was persisted")
	}
}

func TestAuthorizeRefreshesExpiredCredentialAndProducesDPoP(t *testing.T) {
	home := t.TempDir()
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "key.pem"))
	if err != nil {
		t.Fatal(err)
	}
	var tokenCalls int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v2/recorders/token" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		tokenCalls++
		_ = json.NewEncoder(w).Encode(TokenResponse{AccessToken: "fresh-token", TokenType: "DPoP", ExpiresIn: 600, Scope: "recorder.write gate.check", DeviceID: "rec_12345678", TrustState: "active", KeyThumbprint: store.Thumbprint(), ConfigSigningJWK: store.PublicJWK(), ConfigSigningKID: store.Thumbprint()})
	}))
	defer server.Close()
	manager := New(server.URL, home, server.Client(), store)
	if err := manager.saveState(State{Version: 1, DeviceID: "rec_12345678", KeyThumbprint: store.Thumbprint(), ConfigSigningJWK: store.PublicJWK(), ConfigSigningKID: store.Thumbprint()}); err != nil {
		t.Fatal(err)
	}
	scheme, token, made, err := manager.Authorize(context.Background(), "POST", server.URL+"/api/v1/developer/sessions")
	if err != nil {
		t.Fatal(err)
	}
	if scheme != "DPoP" || token != "fresh-token" || made == "" || tokenCalls != 1 {
		t.Fatalf("authorization = %q %q calls=%d", scheme, token, tokenCalls)
	}
	stateBody, err := os.ReadFile(filepath.Join(home, "enterprise-credentials.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(stateBody), "fresh-token") {
		t.Fatal("short-lived access token was persisted")
	}
}

func TestAuthorizeRejectsSigningTrustAnchorChange(t *testing.T) {
	home := t.TempDir()
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "key.pem"))
	if err != nil {
		t.Fatal(err)
	}
	other, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "other.pem"))
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(TokenResponse{AccessToken: "fresh-token", TokenType: "DPoP", ExpiresIn: 600, Scope: "recorder.write gate.check", DeviceID: "rec_12345678", TrustState: "active", KeyThumbprint: store.Thumbprint(), ConfigSigningJWK: other.PublicJWK(), ConfigSigningKID: other.Thumbprint()})
	}))
	defer server.Close()
	manager := New(server.URL, home, server.Client(), store)
	if err := manager.saveState(State{Version: 1, DeviceID: "rec_12345678", KeyThumbprint: store.Thumbprint(), ConfigSigningJWK: store.PublicJWK(), ConfigSigningKID: store.Thumbprint(), ConfigVersion: 4, ConfigDigest: "trusted"}); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := manager.Authorize(context.Background(), "POST", server.URL+"/api/v1/developer/sessions"); err == nil {
		t.Fatal("changed configuration signing key was accepted")
	}
	state, err := manager.loadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.ConfigVersion != 4 || state.ConfigDigest != "trusted" {
		t.Fatal("trusted configuration state was overwritten")
	}
}

func TestFetchedConfigurationIsNotReportedAppliedUntilRuntimeActivation(t *testing.T) {
	home := t.TempDir()
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "key.pem"))
	if err != nil {
		t.Fatal(err)
	}
	manager := New("https://mesh.example", home, nil, store)
	if err := manager.saveState(State{
		Version: 1, DeviceID: "rec_12345678", KeyThumbprint: store.Thumbprint(),
		ConfigSigningJWK: store.PublicJWK(), ConfigSigningKID: store.Thumbprint(),
		ConfigVersion: 2, ConfigDigest: "verified-v2",
		AppliedConfigVersion: 1, AppliedConfigDigest: "applied-v1",
	}); err != nil {
		t.Fatal(err)
	}
	if got := manager.ConfigVersion(); got != 1 {
		t.Fatalf("reported fetched version as applied: %d", got)
	}
	signed := SignedConfig{Payload: map[string]any{"version": float64(2)}, DigestSHA256: "verified-v2"}
	if err := manager.MarkConfigApplied(signed); err != nil {
		t.Fatal(err)
	}
	if got := manager.ConfigVersion(); got != 2 {
		t.Fatalf("applied version = %d", got)
	}
}

func TestVerifySignedConfigurationRejectsTamper(t *testing.T) {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	jwk := map[string]string{"kty": "EC", "crv": "P-256", "x": b64(key.X.FillBytes(make([]byte, 32))), "y": b64(key.Y.FillBytes(make([]byte, 32)))}
	kid := "trusted"
	claims := map[string]any{"protocol": "meshagent.recorder.config.v1", "device_id": "rec_1", "version": 1, "expires_at": float64(time.Now().Add(time.Hour).Unix())}
	header, _ := json.Marshal(map[string]any{"typ": "meshagent-recorder-config+jwt", "alg": "ES256", "kid": kid})
	body, _ := json.Marshal(claims)
	input := b64(header) + "." + b64(body)
	digest := sha256.Sum256([]byte(input))
	r, s, _ := ecdsa.Sign(rand.Reader, key, digest[:])
	raw := append(r.FillBytes(make([]byte, 32)), s.FillBytes(make([]byte, 32))...)
	token := input + "." + b64(raw)
	if _, err := verifyConfigJWT(token, jwk, kid, "rec_1", time.Now()); err != nil {
		t.Fatal(err)
	}
	bad := input + "." + b64(append(big.NewInt(1).FillBytes(make([]byte, 32)), big.NewInt(1).FillBytes(make([]byte, 32))...))
	if _, err := verifyConfigJWT(bad, jwk, kid, "rec_1", time.Now()); err == nil {
		t.Fatal("tampered signature accepted")
	}
}

func TestVerifySignedConfigurationRejectsExpiry(t *testing.T) {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	jwk := map[string]string{"kty": "EC", "crv": "P-256", "x": b64(key.X.FillBytes(make([]byte, 32))), "y": b64(key.Y.FillBytes(make([]byte, 32)))}
	claims := map[string]any{"protocol": "meshagent.recorder.config.v1", "device_id": "rec_1", "version": 1, "expires_at": float64(time.Now().Add(-time.Second).Unix())}
	header, _ := json.Marshal(map[string]any{"typ": "meshagent-recorder-config+jwt", "alg": "ES256", "kid": "trusted"})
	body, _ := json.Marshal(claims)
	input := b64(header) + "." + b64(body)
	digest := sha256.Sum256([]byte(input))
	r, s, _ := ecdsa.Sign(rand.Reader, key, digest[:])
	token := input + "." + b64(append(r.FillBytes(make([]byte, 32)), s.FillBytes(make([]byte, 32))...))
	if _, err := verifyConfigJWT(token, jwk, "trusted", "rec_1", time.Now()); err == nil {
		t.Fatal("expired signed configuration was accepted")
	}
}
