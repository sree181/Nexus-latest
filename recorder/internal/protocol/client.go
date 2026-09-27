package protocol

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/canonicaljson"
)

type Client struct {
	Origin      string
	DeviceToken string
	LocalUser   string
	Identity    func() (deviceToken, localUser string)
	HTTP        *http.Client
	Authorizer  interface {
		Authorize(context.Context, string, string) (scheme, token, proof string, err error)
	}
}

type HTTPError struct{ StatusCode int }

func (e *HTTPError) Error() string { return fmt.Sprintf("server returned HTTP %d", e.StatusCode) }

func IsPermanentValidationError(err error) bool {
	var httpError *HTTPError
	if !errors.As(err, &httpError) {
		return false
	}
	switch httpError.StatusCode {
	case http.StatusBadRequest, http.StatusRequestEntityTooLarge, http.StatusUnsupportedMediaType, http.StatusUnprocessableEntity:
		return true
	default:
		return false
	}
}

func IsTrustError(err error) bool {
	var httpError *HTTPError
	return errors.As(err, &httpError) && (httpError.StatusCode == http.StatusUnauthorized ||
		httpError.StatusCode == http.StatusForbidden)
}

func (c Client) Post(ctx context.Context, envelope Envelope) (map[string]any, error) {
	if c.HTTP == nil {
		c.HTTP = &http.Client{Timeout: 2 * time.Second}
	}
	httpClient := *c.HTTP
	httpClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	target := strings.TrimSuffix(c.Origin, "/") + envelope.Path
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, target,
		bytes.NewReader(envelope.Body),
	)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	key, err := BatchKey(envelope)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Idempotency-Key", key)
	if c.Authorizer != nil {
		scheme, token, proof, err := c.Authorizer.Authorize(ctx, http.MethodPost, target)
		if err != nil {
			return nil, err
		}
		request.Header.Set("Authorization", scheme+" "+token)
		request.Header.Set("DPoP", proof)
	} else {
		token, user := c.DeviceToken, c.LocalUser
		if c.Identity != nil {
			token, user = c.Identity()
		}
		if token != "" {
			request.Header.Set("Authorization", "Bearer "+token)
		} else if user != "" {
			request.Header.Set("X-MeshAgent-User", user)
		}
	}

	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 2*1024*1024+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 2*1024*1024 {
		return nil, fmt.Errorf("response exceeds 2 MiB")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &HTTPError{StatusCode: response.StatusCode}
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var decoded map[string]any
	if err := decoder.Decode(&decoded); err != nil {
		return nil, err
	}
	if !Acknowledged(envelope, decoded) {
		return nil, fmt.Errorf("server response did not acknowledge envelope")
	}
	return decoded, nil
}

func BatchKey(envelope Envelope) (string, error) {
	encoded, err := json.Marshal(envelope)
	if err != nil {
		return "", err
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	var generic any
	if err := decoder.Decode(&generic); err != nil {
		return "", err
	}
	canonical, err := canonicaljson.Marshal(generic)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return "batch_" + hex.EncodeToString(digest[:]), nil
}
