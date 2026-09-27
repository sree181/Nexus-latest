package keystore

import (
	"crypto"
)

// Store is the native trust boundary. Production adapters must keep the private
// P-256 key and queue wrapping key non-exportable. The software implementation
// is deliberately labelled development and cannot satisfy enterprise policy.
type Store interface {
	Signer() crypto.Signer
	PublicJWK() map[string]string
	Thumbprint() string
	Wrap(plaintext []byte, context string) ([]byte, error)
	Unwrap(ciphertext []byte, context string) ([]byte, error)
	Mode() string
}
