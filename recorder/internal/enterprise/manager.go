package enterprise

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/keystore"
)

type State struct {
	Version              int               `json:"version"`
	DeviceID             string            `json:"device_id"`
	KeyThumbprint        string            `json:"key_thumbprint"`
	ConfigSigningJWK     map[string]string `json:"config_signing_jwk"`
	ConfigSigningKID     string            `json:"config_signing_kid"`
	ConfigVersion        int               `json:"config_version"`
	ConfigDigest         string            `json:"config_digest"`
	AppliedConfigVersion int               `json:"applied_config_version"`
	AppliedConfigDigest  string            `json:"applied_config_digest"`
}

type Enrollment struct {
	Label           string `json:"label"`
	Deployment      string `json:"deployment"`
	Platform        string `json:"platform"`
	PlatformVersion string `json:"platform_version"`
	Architecture    string `json:"architecture"`
	RecorderVersion string `json:"recorder_version"`
}

type EnrollmentStart struct {
	EnrollmentID string `json:"enrollment_id"`
	DeviceCode   string `json:"device_code"`
	UserCode     string `json:"user_code"`
	ExpiresIn    int    `json:"expires_in"`
	Interval     int    `json:"interval"`
	VerifyURL    string `json:"verify_url"`
}

type TokenResponse struct {
	AccessToken      string            `json:"access_token"`
	TokenType        string            `json:"token_type"`
	ExpiresIn        int               `json:"expires_in"`
	Scope            string            `json:"scope"`
	DeviceID         string            `json:"device_id"`
	TrustState       string            `json:"trust_state"`
	KeyThumbprint    string            `json:"key_thumbprint"`
	ConfigSigningJWK map[string]string `json:"config_signing_jwk"`
	ConfigSigningKID string            `json:"config_signing_kid"`
}

type SignedConfig struct {
	Payload      map[string]any `json:"payload"`
	DigestSHA256 string         `json:"digest_sha256"`
	Signature    string         `json:"signature"`
	KID          string         `json:"kid"`
}

type HTTPStatusError struct{ Status int }

func (e HTTPStatusError) Error() string {
	return fmt.Sprintf("enterprise endpoint returned HTTP %d", e.Status)
}

func EnrollmentPollRetryable(err error) bool {
	var status HTTPStatusError
	if errors.As(err, &status) {
		return status.Status == http.StatusConflict || status.Status >= 500
	}
	var network net.Error
	return errors.As(err, &network)
}

type Manager struct {
	Origin      string
	Home        string
	HTTP        *http.Client
	KeyStore    keystore.Store
	Now         func() time.Time
	statePath   string
	configPath  string
	mu          sync.Mutex
	accessToken string
	expiresAt   int64
}

func New(origin, home string, client *http.Client, store keystore.Store) *Manager {
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	return &Manager{Origin: strings.TrimSuffix(origin, "/"), Home: home, HTTP: client, KeyStore: store, Now: time.Now, statePath: filepath.Join(home, "enterprise-credentials.json"), configPath: filepath.Join(home, "enterprise-config.json")}
}

func LoadDevelopment(origin, home string, client *http.Client) (*Manager, error) {
	environment := strings.TrimSpace(os.Getenv("MESHAGENT_ENV"))
	if environment == "" {
		environment = "development"
	}
	if environment == "production" {
		return nil, errors.New("production enterprise mode requires a verified OS-backed key-store adapter; software keys are never accepted")
	}
	if environment != "development" && environment != "test" {
		return nil, errors.New("MESHAGENT_ENV must be one of development, test, or production")
	}
	store, err := keystore.LoadOrCreateSoftware(filepath.Join(home, "enterprise-device-key.pem"))
	if err != nil {
		return nil, err
	}
	return New(origin, home, client, store), nil
}

func (m *Manager) Enrolled() bool {
	state, err := m.loadState()
	return err == nil && state.DeviceID != ""
}

func (m *Manager) KeyProtection() string { return m.KeyStore.Mode() }

func (m *Manager) BeginEnrollment(ctx context.Context, metadata Enrollment) (EnrollmentStart, error) {
	body := map[string]any{
		"label": metadata.Label, "deployment": metadata.Deployment,
		"platform": metadata.Platform, "platform_version": metadata.PlatformVersion,
		"architecture": metadata.Architecture, "recorder_version": metadata.RecorderVersion,
		"public_jwk": m.KeyStore.PublicJWK(), "attestation_format": "none",
	}
	var start EnrollmentStart
	if err := m.jsonRequest(ctx, http.MethodPost, "/api/v2/recorders/enrollments", body, nil, &start); err != nil {
		return start, err
	}
	return start, nil
}

func (m *Manager) Claim(ctx context.Context, deviceCode string) (TokenResponse, error) {
	return m.token(ctx, map[string]string{"device_code": deviceCode})
}

func (m *Manager) Refresh(ctx context.Context) (TokenResponse, error) {
	state, err := m.loadState()
	if err != nil {
		return TokenResponse{}, err
	}
	return m.token(ctx, map[string]string{"device_id": state.DeviceID})
}

func (m *Manager) token(ctx context.Context, body map[string]string) (TokenResponse, error) {
	target := m.Origin + "/api/v2/recorders/token"
	made, err := proof(m.KeyStore.Signer(), m.KeyStore.PublicJWK(), http.MethodPost, target, "", m.clock())
	if err != nil {
		return TokenResponse{}, err
	}
	var response TokenResponse
	if err := m.jsonRequest(ctx, http.MethodPost, "/api/v2/recorders/token", body, map[string]string{"DPoP": made}, &response); err != nil {
		return response, err
	}
	if response.TokenType != "DPoP" || response.Scope != "recorder.write gate.check" || response.ExpiresIn != 600 || response.TrustState != "active" || response.KeyThumbprint != m.KeyStore.Thumbprint() {
		return response, errors.New("recorder token response did not match the enrolled key or policy")
	}
	prior, priorErr := m.loadState()
	if priorErr == nil {
		if prior.DeviceID != response.DeviceID || prior.ConfigSigningKID != response.ConfigSigningKID || !equalJWK(prior.ConfigSigningJWK, response.ConfigSigningJWK) {
			return response, errors.New("recorder token response attempted an untrusted identity or signing-key change")
		}
	}
	state := State{
		Version: 1, DeviceID: response.DeviceID, KeyThumbprint: response.KeyThumbprint,
		ConfigSigningJWK: response.ConfigSigningJWK, ConfigSigningKID: response.ConfigSigningKID,
	}
	if priorErr == nil {
		state.ConfigVersion = prior.ConfigVersion
		state.ConfigDigest = prior.ConfigDigest
		state.AppliedConfigVersion = prior.AppliedConfigVersion
		state.AppliedConfigDigest = prior.AppliedConfigDigest
	}
	if err := m.saveState(state); err != nil {
		return response, err
	}
	m.accessToken = response.AccessToken
	m.expiresAt = m.clock().Unix() + int64(response.ExpiresIn)
	return response, nil
}

func (m *Manager) Authorize(ctx context.Context, method, target string) (scheme, token, made string, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	state, err := m.loadState()
	if err != nil {
		return "", "", "", err
	}
	if m.expiresAt <= m.clock().Add(60*time.Second).Unix() || m.accessToken == "" {
		if _, err := m.token(ctx, map[string]string{"device_id": state.DeviceID}); err != nil {
			return "", "", "", err
		}
		state, err = m.loadState()
		if err != nil {
			return "", "", "", err
		}
	}
	made, err = proof(m.KeyStore.Signer(), m.KeyStore.PublicJWK(), method, target, m.accessToken, m.clock())
	return "DPoP", m.accessToken, made, err
}

func (m *Manager) FetchConfig(ctx context.Context) (SignedConfig, error) {
	path := "/api/v2/recorders/config"
	target := m.Origin + path
	scheme, token, made, err := m.Authorize(ctx, http.MethodGet, target)
	if err != nil {
		return SignedConfig{}, err
	}
	var response SignedConfig
	if err := m.jsonRequest(ctx, http.MethodGet, path, nil, map[string]string{"Authorization": scheme + " " + token, "DPoP": made}, &response); err != nil {
		return response, err
	}
	state, err := m.loadState()
	if err != nil {
		return response, err
	}
	claims, err := verifyConfigJWT(response.Signature, state.ConfigSigningJWK, state.ConfigSigningKID, state.DeviceID, m.clock())
	if err != nil {
		return response, err
	}
	canonical, err := json.Marshal(claims)
	if err != nil {
		return response, err
	}
	payloadCanonical, err := json.Marshal(response.Payload)
	if err != nil || !bytes.Equal(canonical, payloadCanonical) {
		return response, errors.New("signed configuration payload does not match its signature")
	}
	digest := sha256.Sum256(canonical)
	if hex.EncodeToString(digest[:]) != response.DigestSHA256 {
		return response, errors.New("signed configuration digest mismatch")
	}
	version, ok := claims["version"].(float64)
	if !ok || int(version) < state.ConfigVersion {
		return response, errors.New("signed configuration version rolled back")
	}
	if int(version) == state.ConfigVersion && state.ConfigDigest != "" && state.ConfigDigest != response.DigestSHA256 {
		return response, errors.New("signed configuration changed without a version increment")
	}
	state.ConfigVersion = int(version)
	state.ConfigDigest = response.DigestSHA256
	if err := m.saveState(state); err != nil {
		return response, err
	}
	if err := m.savePrivateJSON(m.configPath, response); err != nil {
		return response, err
	}
	return response, nil
}

// LoadConfig prefers a fresh control-plane snapshot and falls back only to a
// still-valid, locally verified signed snapshot. It never accepts unsigned
// environment policy in enterprise mode.
func (m *Manager) LoadConfig(ctx context.Context) (SignedConfig, error) {
	fresh, err := m.FetchConfig(ctx)
	if err == nil {
		return fresh, nil
	}
	var cached SignedConfig
	body, readErr := os.ReadFile(m.configPath)
	if readErr != nil {
		return cached, err
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decodeErr := decoder.Decode(&cached); decodeErr != nil {
		return cached, errors.New("cached signed configuration is invalid")
	}
	state, stateErr := m.loadState()
	if stateErr != nil {
		return cached, stateErr
	}
	claims, verifyErr := verifyConfigJWT(cached.Signature, state.ConfigSigningJWK, state.ConfigSigningKID, state.DeviceID, m.clock())
	if verifyErr != nil {
		return cached, verifyErr
	}
	canonical, _ := json.Marshal(claims)
	payloadCanonical, _ := json.Marshal(cached.Payload)
	digest := sha256.Sum256(canonical)
	if !bytes.Equal(canonical, payloadCanonical) || hex.EncodeToString(digest[:]) != cached.DigestSHA256 {
		return cached, errors.New("cached signed configuration integrity failed")
	}
	version, ok := claims["version"].(float64)
	if !ok || int(version) < state.ConfigVersion {
		return cached, errors.New("cached signed configuration version rolled back")
	}
	if int(version) == state.ConfigVersion && state.ConfigDigest != "" && state.ConfigDigest != cached.DigestSHA256 {
		return cached, errors.New("cached signed configuration changed without a version increment")
	}
	return cached, nil
}

func (m *Manager) PostHeartbeat(ctx context.Context, payload any) error {
	path := "/api/v2/recorders/heartbeat"
	target := m.Origin + path
	scheme, token, made, err := m.Authorize(ctx, http.MethodPost, target)
	if err != nil {
		return err
	}
	var receipt map[string]any
	return m.jsonRequest(ctx, http.MethodPost, path, payload, map[string]string{"Authorization": scheme + " " + token, "DPoP": made}, &receipt)
}

func (m *Manager) ConfigVersion() int {
	state, err := m.loadState()
	if err != nil {
		return 0
	}
	return state.AppliedConfigVersion
}

// MarkConfigApplied advances only after the daemon has successfully applied
// every effective value from this exact verified snapshot to its runtime.
func (m *Manager) MarkConfigApplied(config SignedConfig) error {
	state, err := m.loadState()
	if err != nil {
		return err
	}
	version, ok := config.Payload["version"].(float64)
	if !ok || int(version) != state.ConfigVersion || config.DigestSHA256 != state.ConfigDigest {
		return errors.New("applied configuration does not match the latest verified snapshot")
	}
	if int(version) < state.AppliedConfigVersion {
		return errors.New("applied configuration version rolled back")
	}
	if int(version) == state.AppliedConfigVersion && state.AppliedConfigDigest != "" && state.AppliedConfigDigest != config.DigestSHA256 {
		return errors.New("applied configuration changed without a version increment")
	}
	state.AppliedConfigVersion = int(version)
	state.AppliedConfigDigest = config.DigestSHA256
	return m.saveState(state)
}

func (m *Manager) RefreshConfiguration(ctx context.Context) error {
	_, err := m.LoadConfig(ctx)
	return err
}

func (m *Manager) State() (State, error) { return m.loadState() }

func (m *Manager) jsonRequest(ctx context.Context, method, path string, body any, headers map[string]string, out any) error {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, m.Origin+path, reader)
	if err != nil {
		return err
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	client := *m.HTTP
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	encoded, err := io.ReadAll(io.LimitReader(response.Body, 1024*1024+1))
	if err != nil {
		return err
	}
	if len(encoded) > 1024*1024 {
		return errors.New("enterprise response exceeds 1 MiB")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return HTTPStatusError{Status: response.StatusCode}
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	return decoder.Decode(out)
}

func (m *Manager) loadState() (State, error) {
	var state State
	body, err := os.ReadFile(m.statePath)
	if err != nil {
		return state, err
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&state); err != nil {
		return state, err
	}
	if state.Version != 1 || state.DeviceID == "" || state.KeyThumbprint != m.KeyStore.Thumbprint() {
		return state, errors.New("enterprise credential state is invalid for this key")
	}
	return state, nil
}

func (m *Manager) saveState(state State) error {
	return m.savePrivateJSON(m.statePath, state)
}

func (m *Manager) savePrivateJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".meshagent-private-")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(append(encoded, '\n')); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

func (m *Manager) clock() time.Time {
	if m.Now != nil {
		return m.Now()
	}
	return time.Now()
}

func equalJWK(left, right map[string]string) bool {
	return left["kty"] == right["kty"] && left["crv"] == right["crv"] && left["x"] == right["x"] && left["y"] == right["y"]
}

func DefaultEnrollment(label, deployment, version string) Enrollment {
	platformVersion := strings.TrimSpace(os.Getenv("MESHAGENT_PLATFORM_VERSION"))
	if platformVersion == "" {
		platformVersion = "unknown"
	}
	return Enrollment{Label: label, Deployment: deployment, Platform: runtime.GOOS, PlatformVersion: platformVersion, Architecture: runtime.GOARCH, RecorderVersion: version}
}
