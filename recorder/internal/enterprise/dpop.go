package enterprise

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"time"
)

func b64(data []byte) string { return base64.RawURLEncoding.EncodeToString(data) }

func proof(signer crypto.Signer, publicJWK map[string]string, method, target, token string, now time.Time) (string, error) {
	header, err := json.Marshal(map[string]any{"typ": "dpop+jwt", "alg": "ES256", "jwk": publicJWK})
	if err != nil {
		return "", err
	}
	jti, err := randomID()
	if err != nil {
		return "", err
	}
	claims := map[string]any{
		"htm": strings.ToUpper(method), "htu": target, "iat": now.Unix(), "jti": jti,
	}
	if token != "" {
		digest := sha256.Sum256([]byte(token))
		claims["ath"] = b64(digest[:])
	}
	body, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	input := b64(header) + "." + b64(body)
	digest := sha256.Sum256([]byte(input))
	signature, err := signer.Sign(rand.Reader, digest[:], crypto.SHA256)
	if err != nil {
		return "", err
	}
	// crypto/ecdsa returns ASN.1 through Signer. JWS ES256 needs raw R||S.
	var parsed struct{ R, S *big.Int }
	if _, err := asn1Unmarshal(signature, &parsed); err != nil || parsed.R == nil || parsed.S == nil {
		return "", errors.New("signing key returned an invalid ECDSA signature")
	}
	raw := append(parsed.R.FillBytes(make([]byte, 32)), parsed.S.FillBytes(make([]byte, 32))...)
	return input + "." + b64(raw), nil
}

func verifyConfigJWT(compact string, jwk map[string]string, expectedKID, deviceID string, now time.Time) (map[string]any, error) {
	parts := strings.Split(compact, ".")
	if len(parts) != 3 {
		return nil, errors.New("signed configuration is malformed")
	}
	var header map[string]any
	if err := decodeJSONPart(parts[0], &header); err != nil {
		return nil, err
	}
	if header["alg"] != "ES256" || header["typ"] != "meshagent-recorder-config+jwt" || header["kid"] != expectedKID {
		return nil, errors.New("signed configuration header is not trusted")
	}
	key, err := publicKey(jwk)
	if err != nil {
		return nil, err
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil || len(raw) != 64 {
		return nil, errors.New("signed configuration has an invalid signature")
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if !ecdsa.Verify(key, digest[:], new(big.Int).SetBytes(raw[:32]), new(big.Int).SetBytes(raw[32:])) {
		return nil, errors.New("signed configuration signature rejected")
	}
	var claims map[string]any
	if err := decodeJSONPart(parts[1], &claims); err != nil {
		return nil, err
	}
	if claims["device_id"] != deviceID || claims["protocol"] != "meshagent.recorder.config.v1" {
		return nil, errors.New("signed configuration does not match this recorder")
	}
	expires, ok := claims["expires_at"].(float64)
	if !ok || int64(expires) <= now.Unix() {
		return nil, errors.New("signed configuration is expired")
	}
	return claims, nil
}

func publicKey(jwk map[string]string) (*ecdsa.PublicKey, error) {
	if jwk["kty"] != "EC" || jwk["crv"] != "P-256" {
		return nil, errors.New("configuration key is not P-256")
	}
	x, errX := base64.RawURLEncoding.DecodeString(jwk["x"])
	y, errY := base64.RawURLEncoding.DecodeString(jwk["y"])
	if errX != nil || errY != nil || len(x) != 32 || len(y) != 32 {
		return nil, errors.New("configuration key coordinates are invalid")
	}
	key := &ecdsa.PublicKey{Curve: elliptic.P256(), X: new(big.Int).SetBytes(x), Y: new(big.Int).SetBytes(y)}
	if !key.Curve.IsOnCurve(key.X, key.Y) {
		return nil, errors.New("configuration key is not on P-256")
	}
	return key, nil
}

func decodeJSONPart(part string, value any) error {
	body, err := base64.RawURLEncoding.DecodeString(part)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(body, value); err != nil {
		return err
	}
	return nil
}

func randomID() (string, error) {
	body := make([]byte, 16)
	if _, err := rand.Read(body); err != nil {
		return "", err
	}
	return b64(body), nil
}
