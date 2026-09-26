//go:build windows

package ipc

import (
	"context"
	"fmt"
	"net"
	"os/user"

	"github.com/Microsoft/go-winio"
)

func listen(address string) (net.Listener, func(), error) {
	current, err := user.Current()
	if err != nil {
		return nil, func() {}, err
	}
	listener, err := winio.ListenPipe(address, &winio.PipeConfig{
		SecurityDescriptor: fmt.Sprintf("D:P(A;;GA;;;%s)", current.Uid),
		InputBufferSize:    MaxFrameBytes,
		OutputBufferSize:   MaxFrameBytes,
	})
	if err != nil {
		return nil, func() {}, err
	}
	return listener, func() { _ = listener.Close() }, nil
}

func dial(ctx context.Context, address string) (net.Conn, error) {
	return winio.DialPipeContext(ctx, address)
}
