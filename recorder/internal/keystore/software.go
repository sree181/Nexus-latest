package keystore

import (
	"crypto"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type Software struct {
	key  *ecdsa.PrivateKey
	path string
}

func LoadOrCreateSoftware(path string) (*Software, error) {
	if body, err := os.ReadFile(path); err == nil {
		block, _ := pem.Decode(body)
		if block == nil {
			return nil, errors.New("development device key is invalid")
		}
		parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
		key, ok := parsed.(*ecdsa.PrivateKey)
		if err != nil || !ok || key.Curve != elliptic.P256() {
			return nil, errors.New("development device key is not P-256")
		}
		if err := os.Chmod(path, 0o600); err != nil {
			return nil, err
		}
		return &Software{key: key, path: path}, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	encoded, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return nil, err
	}
	if err := pem.Encode(file, &pem.Block{Type: "PRIVATE KEY", Bytes: encoded}); err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := file.Close(); err != nil {
		return nil, err
	}
	return &Software{key: key, path: path}, nil
}

func (s *Software) Signer() crypto.Signer { return s.key }
func (s *Software) Mode() string          { return "software-development" }

func (s *Software) PublicJWK() map[string]string {
	return map[string]string{
		"kty": "EC", "crv": "P-256",
		"x": base64.RawURLEncoding.EncodeToString(s.key.PublicKey.X.FillBytes(make([]byte, 32))),
		"y": base64.RawURLEncoding.EncodeToString(s.key.PublicKey.Y.FillBytes(make([]byte, 32))),
	}
}

func (s *Software) Thumbprint() string {
	canonical, _ := json.Marshal(struct {
		CRV string `json:"crv"`
		KTY string `json:"kty"`
		X   string `json:"x"`
		Y   string `json:"y"`
	}{CRV: "P-256", KTY: "EC", X: s.PublicJWK()["x"], Y: s.PublicJWK()["y"]})
	digest := sha256.Sum256(canonical)
	return base64.RawURLEncoding.EncodeToString(digest[:])
}

func (s *Software) wrapKey() []byte {
	material := append([]byte("meshagent-recorder-queue-wrap-v1\x00"), s.key.D.FillBytes(make([]byte, 32))...)
	digest := sha256.Sum256(material)
	return digest[:]
}

func (s *Software) Wrap(plaintext []byte, context string) ([]byte, error) {
	block, err := aes.NewCipher(s.wrapKey())
	if err != nil {
		return nil, err
	}
	box, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, box.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return append(nonce, box.Seal(nil, nonce, plaintext, []byte(context))...), nil
}

func (s *Software) Unwrap(ciphertext []byte, context string) ([]byte, error) {
	block, err := aes.NewCipher(s.wrapKey())
	if err != nil {
		return nil, err
	}
	box, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(ciphertext) < box.NonceSize() {
		return nil, fmt.Errorf("wrapped queue key is truncated")
	}
	return box.Open(nil, ciphertext[:box.NonceSize()], ciphertext[box.NonceSize():], []byte(context))
}
