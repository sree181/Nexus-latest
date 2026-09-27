package enterprise

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/sree181/Nexus-latest/recorder/internal/keystore"
)

type wrappedKey struct {
	Version    int    `json:"version"`
	Protection string `json:"protection"`
	Ciphertext string `json:"ciphertext"`
}

func LoadOrCreateQueueKey(home, databasePath string, store keystore.Store) ([]byte, string, error) {
	path := filepath.Join(home, "recorder.queue-key.wrapped")
	legacyPath := filepath.Join(home, "recorder.queue-key")
	context := "meshagent-recorder-queue-v1|" + filepath.Base(databasePath)
	if body, err := os.ReadFile(path); err == nil {
		var wrapped wrappedKey
		if json.Unmarshal(body, &wrapped) != nil || wrapped.Version != 1 || wrapped.Protection != store.Mode() {
			return nil, "", errors.New("wrapped recorder queue key metadata is invalid")
		}
		ciphertext, err := base64.RawURLEncoding.DecodeString(wrapped.Ciphertext)
		if err != nil {
			return nil, "", errors.New("wrapped recorder queue key is invalid")
		}
		key, err := store.Unwrap(ciphertext, context)
		if err != nil || len(key) != 32 {
			return nil, "", errors.New("wrapped recorder queue key could not be recovered")
		}
		return key, store.Mode(), nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, "", err
	}
	if encoded, err := os.ReadFile(legacyPath); err == nil {
		key, decodeErr := base64.RawURLEncoding.DecodeString(string(bytesTrimSpace(encoded)))
		if decodeErr != nil || len(key) != 32 {
			return nil, "", errors.New("existing development queue key is invalid")
		}
		if err := persistWrapped(path, store, context, key); err != nil {
			return nil, "", err
		}
		if err := os.Remove(legacyPath); err != nil {
			return nil, "", fmt.Errorf("remove migrated development queue key: %w", err)
		}
		return key, store.Mode(), nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, "", err
	}
	if _, err := os.Stat(databasePath); err == nil {
		return nil, "", errors.New("wrapped queue key is missing for an existing recorder database")
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, "", err
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, "", err
	}
	if err := persistWrapped(path, store, context, key); err != nil {
		return nil, "", err
	}
	return key, store.Mode(), nil
}

func persistWrapped(path string, store keystore.Store, context string, key []byte) error {
	ciphertext, err := store.Wrap(key, context)
	if err != nil {
		return err
	}
	body, err := json.Marshal(wrappedKey{Version: 1, Protection: store.Mode(), Ciphertext: base64.RawURLEncoding.EncodeToString(ciphertext)})
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fmt.Errorf("create wrapped queue key: %w", err)
	}
	if _, err := file.Write(append(body, '\n')); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return nil
}

func bytesTrimSpace(value []byte) []byte {
	start, end := 0, len(value)
	for start < end && (value[start] == ' ' || value[start] == '\n' || value[start] == '\r' || value[start] == '\t') {
		start++
	}
	for end > start && (value[end-1] == ' ' || value[end-1] == '\n' || value[end-1] == '\r' || value[end-1] == '\t') {
		end--
	}
	return value[start:end]
}
