//go:build !windows

package ipc

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"time"
)

type authorizedListener struct{ net.Listener }

func (l authorizedListener) Accept() (net.Conn, error) {
	for {
		conn, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		if err := authorizePeer(conn); err != nil {
			_ = conn.Close()
			continue
		}
		return conn, nil
	}
}

func listen(address string) (net.Listener, func(), error) {
	if err := os.MkdirAll(filepath.Dir(address), 0o700); err != nil {
		return nil, func() {}, err
	}
	if err := os.Chmod(filepath.Dir(address), 0o700); err != nil {
		return nil, func() {}, err
	}
	if existing, err := os.Lstat(address); err == nil {
		if existing.Mode()&os.ModeSocket == 0 || existing.Mode()&os.ModeSymlink != 0 {
			return nil, func() {}, fmt.Errorf("IPC address exists and is not a socket: %s", address)
		}
		if err := os.Remove(address); err != nil {
			return nil, func() {}, err
		}
	} else if !os.IsNotExist(err) {
		return nil, func() {}, err
	}
	listener, err := net.Listen("unix", address)
	if err != nil {
		return nil, func() {}, err
	}
	if err := os.Chmod(address, 0o600); err != nil {
		_ = listener.Close()
		return nil, func() {}, err
	}
	owned, err := os.Stat(address)
	if err != nil {
		_ = listener.Close()
		return nil, func() {}, err
	}
	cleanup := func() {
		_ = listener.Close()
		if current, err := os.Stat(address); err == nil && os.SameFile(owned, current) {
			_ = os.Remove(address)
		}
	}
	return authorizedListener{Listener: listener}, cleanup, nil
}

func dial(ctx context.Context, address string) (net.Conn, error) {
	dialer := net.Dialer{Timeout: 2 * time.Second}
	return dialer.DialContext(ctx, "unix", address)
}
