//go:build darwin

package ipc

import (
	"errors"
	"net"
	"os"

	"golang.org/x/sys/unix"
)

func authorizePeer(conn net.Conn) error {
	unixConn, ok := conn.(*net.UnixConn)
	if !ok {
		return errors.New("IPC connection is not a Unix socket")
	}
	raw, err := unixConn.SyscallConn()
	if err != nil {
		return err
	}
	var peerErr error
	if err := raw.Control(func(fd uintptr) {
		credential, err := unix.GetsockoptXucred(int(fd), unix.SOL_LOCAL, unix.LOCAL_PEERCRED)
		if err != nil {
			peerErr = err
			return
		}
		if int(credential.Uid) != os.Getuid() {
			peerErr = errors.New("IPC peer belongs to a different user")
		}
	}); err != nil {
		return err
	}
	return peerErr
}
