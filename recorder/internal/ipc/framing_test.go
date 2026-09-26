package ipc

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestReadFrameRejectsOversizedPayload(t *testing.T) {
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], MaxFrameBytes+1)
	var value map[string]any
	if err := readFrame(bytes.NewReader(header[:]), &value); err == nil {
		t.Fatal("expected oversized frame error")
	}
}

func TestReadFrameRejectsUnknownTopLevelFields(t *testing.T) {
	var frame bytes.Buffer
	if err := writeFrame(&frame, map[string]any{
		"protocol": Protocol, "request_id": "req-1", "deadline_ms": 1000,
		"editor": "cursor", "event": map[string]any{}, "unexpected": true,
	}); err != nil {
		t.Fatal(err)
	}
	var request Request
	if err := readFrame(&frame, &request); err == nil {
		t.Fatal("expected unknown field rejection")
	}
}

func TestReadFrameRejectsTrailingJSONValue(t *testing.T) {
	body := []byte(`{} {}`)
	var frame bytes.Buffer
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(body)))
	frame.Write(header[:])
	frame.Write(body)
	var request Request
	if err := readFrame(&frame, &request); err == nil {
		t.Fatal("expected trailing JSON rejection")
	}
}
