package protocol

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
)

const Version = "meshagent.session.v1"

type Repository struct {
	ID     string  `json:"id"`
	Name   string  `json:"name"`
	Remote *string `json:"remote,omitempty"`
	Branch *string `json:"branch,omitempty"`
	Commit *string `json:"commit,omitempty"`
}

type SessionStart struct {
	ID              string     `json:"id"`
	SourceSessionID string     `json:"source_session_id"`
	SourceEventID   string     `json:"source_event_id"`
	Adapter         string     `json:"adapter"`
	AdapterVersion  string     `json:"adapter_version"`
	Repository      Repository `json:"repository"`
	Task            string     `json:"task"`
	StartedAtMS     int64      `json:"started_at_ms"`
	Sequence        int        `json:"sequence"`
}

type ActivityEvent struct {
	EventID       string         `json:"event_id"`
	SourceEventID string         `json:"source_event_id"`
	Sequence      int64          `json:"sequence"`
	OccurredAtMS  int64          `json:"occurred_at_ms"`
	Type          string         `json:"type"`
	Payload       map[string]any `json:"payload"`
}

type ActivityBatch struct {
	Events []ActivityEvent `json:"events"`
}

type Envelope struct {
	Protocol  string          `json:"protocol"`
	Kind      string          `json:"kind"`
	Session   string          `json:"session"`
	SessionID string          `json:"session_id"`
	Path      string          `json:"path"`
	Body      json.RawMessage `json:"body"`
}

func NewEnvelope(kind, nativeSession, sessionID, path string, body any) (Envelope, error) {
	encoded, err := json.Marshal(body)
	if err != nil {
		return Envelope{}, err
	}
	return Envelope{
		Protocol: Version, Kind: kind, Session: nativeSession,
		SessionID: sessionID, Path: path, Body: encoded,
	}, nil
}

func (e Envelope) FinalSequence() (int64, error) {
	switch e.Kind {
	case "start":
		var body SessionStart
		if err := json.Unmarshal(e.Body, &body); err != nil {
			return 0, err
		}
		return int64(body.Sequence), nil
	case "events":
		var body ActivityBatch
		if err := json.Unmarshal(e.Body, &body); err != nil {
			return 0, err
		}
		if len(body.Events) == 0 {
			return 0, errors.New("event envelope has no events")
		}
		return body.Events[len(body.Events)-1].Sequence, nil
	default:
		return 0, fmt.Errorf("unsupported envelope kind %q", e.Kind)
	}
}

func Acknowledged(envelope Envelope, response map[string]any) bool {
	if response == nil || envelope.Protocol != Version {
		return false
	}
	if envelope.Kind == "start" {
		ack, ok := integer(response["last_acked_sequence"])
		id, idOK := response["id"].(string)
		return ok && idOK && id == envelope.SessionID && ack >= 1
	}
	if envelope.Kind == "events" {
		expected, err := envelope.FinalSequence()
		if err != nil {
			return false
		}
		ack, ok := integer(response["acknowledged_through"])
		return ok && ack >= expected
	}
	return false
}

func NewSessionID() (string, error) { return opaqueID("ses_") }
func NewEventID() (string, error)   { return opaqueID("evt_") }

func opaqueID(prefix string) (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return prefix + hex.EncodeToString(value), nil
}

func integer(value any) (int64, bool) {
	switch typed := value.(type) {
	case float64:
		if typed != float64(int64(typed)) {
			return 0, false
		}
		return int64(typed), true
	case int:
		return int64(typed), true
	case int64:
		return typed, true
	case json.Number:
		parsed, err := typed.Int64()
		return parsed, err == nil
	default:
		return 0, false
	}
}
