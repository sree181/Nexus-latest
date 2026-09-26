package ipc

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"time"
)

const Protocol = "meshagent.local-hook.v1"
const MaxFrameBytes = 2 * 1024 * 1024
const MaxRequestDuration = 6 * time.Second
const MaxConcurrentConnections = 64

type Request struct {
	Protocol   string         `json:"protocol"`
	RequestID  string         `json:"request_id"`
	DeadlineMS int            `json:"deadline_ms"`
	Editor     string         `json:"editor"`
	Event      map[string]any `json:"event"`
}

type PermissionResponse struct {
	Permission   string `json:"permission"`
	UserMessage  string `json:"user_message,omitempty"`
	AgentMessage string `json:"agent_message,omitempty"`
}

type Response struct {
	Protocol       string              `json:"protocol"`
	RequestID      string              `json:"request_id"`
	Accepted       bool                `json:"accepted"`
	CursorResponse *PermissionResponse `json:"cursor_response,omitempty"`
	DiagnosticCode string              `json:"diagnostic_code,omitempty"`
}

type Handler func(context.Context, Request) Response

func Serve(ctx context.Context, address string, handler Handler) error {
	listener, cleanup, err := listen(address)
	if err != nil {
		return err
	}
	defer cleanup()
	slots := make(chan struct{}, MaxConcurrentConnections)
	go func() {
		<-ctx.Done()
		_ = listener.Close()
	}()
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			continue
		}
		select {
		case slots <- struct{}{}:
			go func() {
				defer func() { <-slots }()
				serveConnection(ctx, conn, handler)
			}()
		default:
			_ = conn.Close()
		}
	}
}

func Send(ctx context.Context, address string, request Request) (Response, error) {
	conn, err := dial(ctx, address)
	if err != nil {
		return Response{}, err
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	}
	if err := writeFrame(conn, request); err != nil {
		return Response{}, err
	}
	var response Response
	if err := readFrame(conn, &response); err != nil {
		return Response{}, err
	}
	if response.Protocol != Protocol || response.RequestID != request.RequestID {
		return Response{}, errors.New("IPC response does not match request")
	}
	return response, nil
}

func serveConnection(parent context.Context, conn net.Conn, handler Handler) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(MaxRequestDuration))
	var request Request
	if err := readFrame(conn, &request); err != nil {
		return
	}
	if request.Protocol != Protocol || request.RequestID == "" || request.Editor != "cursor" {
		_ = writeFrame(conn, Response{Protocol: Protocol, RequestID: request.RequestID, DiagnosticCode: "invalid_request"})
		return
	}
	deadline := request.DeadlineMS
	if deadline < 1 || deadline > 5500 {
		deadline = 1500
	}
	_ = conn.SetDeadline(time.Now().Add(time.Duration(deadline) * time.Millisecond))
	ctx, cancel := context.WithTimeout(parent, time.Duration(deadline)*time.Millisecond)
	defer cancel()
	response := handler(ctx, request)
	response.Protocol = Protocol
	response.RequestID = request.RequestID
	_ = writeFrame(conn, response)
}

func writeFrame(writer io.Writer, value any) error {
	body, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if len(body) == 0 || len(body) > MaxFrameBytes {
		return fmt.Errorf("IPC frame size %d is outside allowed bounds", len(body))
	}
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(body)))
	if _, err := writer.Write(header[:]); err != nil {
		return err
	}
	_, err = writer.Write(body)
	return err
}

func readFrame(reader io.Reader, value any) error {
	var header [4]byte
	if _, err := io.ReadFull(reader, header[:]); err != nil {
		return err
	}
	size := binary.BigEndian.Uint32(header[:])
	if size == 0 || size > MaxFrameBytes {
		return fmt.Errorf("IPC frame size %d is outside allowed bounds", size)
	}
	body := make([]byte, size)
	if _, err := io.ReadFull(reader, body); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytesReader(body))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("IPC frame contains more than one JSON value")
		}
		return err
	}
	return nil
}

type byteReader struct {
	body []byte
	off  int
}

func bytesReader(body []byte) *byteReader { return &byteReader{body: body} }
func (r *byteReader) Read(p []byte) (int, error) {
	if r.off >= len(r.body) {
		return 0, io.EOF
	}
	n := copy(p, r.body[r.off:])
	r.off += n
	return n, nil
}
