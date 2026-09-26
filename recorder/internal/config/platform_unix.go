//go:build !windows

package config

import (
	"os"
	"path/filepath"
	"syscall"
)

func ApplyPrivateUmask() { syscall.Umask(0o077) }

func chmodPrivate(path string, mode os.FileMode) error {
	return os.Chmod(path, mode)
}

func socketPath(home string) string {
	return filepath.Join(home, "run", "recorder.sock")
}
