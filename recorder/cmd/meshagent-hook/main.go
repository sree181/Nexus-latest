package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/config"
	"github.com/sree181/Nexus-latest/recorder/internal/ipc"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

var version = "dev"

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--health" {
		if err := health(); err != nil {
			fmt.Fprintln(os.Stderr, "meshagent-hook: diagnostic=recorder_unavailable")
			os.Exit(1)
		}
		_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"status": "ok", "version": version})
		return
	}
	var event map[string]any
	decoder := json.NewDecoder(os.Stdin)
	decoder.UseNumber()
	if err := decoder.Decode(&event); err != nil {
		writeAllow()
		return
	}
	eventName, _ := event["hook_event_name"].(string)
	isPermission := eventName == "beforeShellExecution"
	runtime, err := config.Load()
	if err != nil {
		if isPermission {
			writeAllow()
		}
		return
	}
	requestID, err := protocol.NewEventID()
	if err != nil {
		if isPermission {
			writeAllow()
		}
		return
	}
	deadlineMS := 1500
	timeout := 2 * time.Second
	if isPermission {
		deadlineMS = runtime.GateTimeoutMS
		timeout = 5200 * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	response, err := ipc.Send(ctx, runtime.SocketPath, ipc.Request{Protocol: ipc.Protocol, RequestID: requestID, DeadlineMS: deadlineMS, Editor: "cursor", Event: event})
	if err != nil {
		fmt.Fprintln(os.Stderr, "meshagent-hook: diagnostic=recorder_unavailable")
		if isPermission {
			writeAllow()
		}
		return
	}
	if isPermission {
		if response.CursorResponse == nil || (response.CursorResponse.Permission != "allow" && response.CursorResponse.Permission != "deny") {
			writeAllow()
			return
		}
		_ = json.NewEncoder(os.Stdout).Encode(response.CursorResponse)
	}
}

func health() error {
	runtime, err := config.Load()
	if err != nil {
		return err
	}
	requestID, err := protocol.NewEventID()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = ipc.Send(ctx, runtime.SocketPath, ipc.Request{
		Protocol: ipc.Protocol, RequestID: requestID, DeadlineMS: 500,
		Editor: "cursor", Event: map[string]any{"hook_event_name": "meshagentHealth"},
	})
	return err
}

func writeAllow() {
	_ = json.NewEncoder(os.Stdout).Encode(ipc.PermissionResponse{Permission: "allow"})
}
