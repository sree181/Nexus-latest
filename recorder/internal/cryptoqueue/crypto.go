package cryptoqueue

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
)

type cipherBox struct {
	aead cipher.AEAD
}

func newCipherBox(key []byte) (*cipherBox, error) {
	if len(key) != 32 {
		return nil, errors.New("queue encryption key must be 32 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &cipherBox{aead: aead}, nil
}

func (c *cipherBox) seal(plaintext []byte, aad string) (ciphertext, nonce []byte, digest string, err error) {
	nonce = make([]byte, c.aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, nil, "", err
	}
	ciphertext = c.aead.Seal(nil, nonce, plaintext, []byte(aad))
	sum := sha256.Sum256(plaintext)
	return ciphertext, nonce, hex.EncodeToString(sum[:]), nil
}

func (c *cipherBox) open(ciphertext, nonce []byte, aad, wantDigest string) ([]byte, error) {
	plaintext, err := c.aead.Open(nil, nonce, ciphertext, []byte(aad))
	if err != nil {
		return nil, errors.New("queue record authentication failed")
	}
	sum := sha256.Sum256(plaintext)
	if hex.EncodeToString(sum[:]) != wantDigest {
		return nil, errors.New("queue record digest mismatch")
	}
	return plaintext, nil
}
