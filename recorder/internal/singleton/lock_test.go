//go:build !windows

package singleton

import (
	"path/filepath"
	"testing"
)

func TestAcquireRejectsSecondOwner(t *testing.T) {
	path := filepath.Join(t.TempDir(), "recorder.lock")
	first, err := Acquire(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := Acquire(path); err == nil {
		_ = second.Close()
		t.Fatal("second owner acquired lock")
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	third, err := Acquire(path)
	if err != nil {
		t.Fatal(err)
	}
	defer third.Close()
}
