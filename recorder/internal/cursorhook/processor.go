package cursorhook

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/sree181/Nexus-latest/recorder/internal/config"
	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/gate"
	"github.com/sree181/Nexus-latest/recorder/internal/installparse"
	"github.com/sree181/Nexus-latest/recorder/internal/ipc"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

const adapter = "cursor"
const adapterVersion = "native-1a"

var unsafeComponent = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
var validEventID = regexp.MustCompile(`^evt_[A-Za-z0-9._-]{16,120}$`)

type Processor struct {
	Runtime config.Runtime
	Store   *cryptoqueue.Store
	Gate    GateClient
	Notify  func()
	Now     func() time.Time
}

type GateClient interface {
	Ask(context.Context, gate.Request) (*gate.Decision, error)
}

type repoConfig struct {
	Record  bool     `json:"record"`
	Gate    *bool    `json:"gate,omitempty"`
	Exclude []string `json:"exclude,omitempty"`
}

func (p *Processor) Handle(ctx context.Context, request ipc.Request) ipc.Response {
	event := request.Event
	eventName := stringValue(event["hook_event_name"])
	workspace := workspace(event)
	cfg, optedIn := readRepoConfig(workspace)
	if eventName == "beforeShellExecution" {
		permission := p.beforeShell(ctx, event, workspace, cfg, optedIn)
		return ipc.Response{Accepted: true, CursorResponse: &permission}
	}
	if !optedIn {
		return ipc.Response{Accepted: false, DiagnosticCode: "repository_not_opted_in"}
	}
	nativeSession := conversation(event)
	if nativeSession == "" {
		return ipc.Response{Accepted: false, DiagnosticCode: "missing_session"}
	}

	drafts, err := p.translate(eventName, event, workspace, cfg, nativeSession)
	if err != nil {
		return ipc.Response{Accepted: false, DiagnosticCode: "observation_rejected"}
	}
	if len(drafts) == 0 {
		return ipc.Response{Accepted: false, DiagnosticCode: "no_observation"}
	}
	if drafts[0]["type"] == "session" && !boolValue(drafts[0]["ends"]) {
		task := stringValue(drafts[0]["task"])
		_, err = p.ensureOpening(ctx, nativeSession, workspace, task, drafts[0])
	} else {
		_, sessionErr := p.Store.Session(ctx, sessionKey(workspace, nativeSession))
		if sessionErr != nil {
			if errors.Is(sessionErr, sql.ErrNoRows) {
				return ipc.Response{Accepted: false, DiagnosticCode: "session_not_open"}
			}
			return ipc.Response{Accepted: false, DiagnosticCode: "queue_unavailable"}
		}
		err = p.enqueueDrafts(ctx, nativeSession, workspace, drafts)
	}
	if err != nil {
		if errors.Is(err, cryptoqueue.ErrQueueFull) {
			return ipc.Response{Accepted: false, DiagnosticCode: "queue_full"}
		}
		if errors.Is(err, cryptoqueue.ErrSessionClosed) {
			return ipc.Response{Accepted: false, DiagnosticCode: "session_closed"}
		}
		return ipc.Response{Accepted: false, DiagnosticCode: "queue_unavailable"}
	}
	p.notify()
	return ipc.Response{Accepted: true}
}

func (p *Processor) beforeShell(ctx context.Context, event map[string]any, workspace string, cfg repoConfig, optedIn bool) ipc.PermissionResponse {
	allow := ipc.PermissionResponse{Permission: "allow"}
	if !optedIn || (cfg.Gate != nil && !*cfg.Gate) {
		return allow
	}
	command := strings.TrimSpace(stringValue(event["command"]))
	if command == "" || p.Gate == nil {
		return allow
	}
	nativeSession := bounded(conversation(event), 256)
	for index, target := range installparse.Parse(command) {
		if index >= 100 || ctx.Err() != nil {
			break
		}
		decision, err := p.Gate.Ask(ctx, gate.Request{Package: target.Name, Version: target.Version, Ecosystem: target.Ecosystem, Session: nativeSession})
		if err != nil || decision == nil {
			continue
		}
		p.recordPolicy(ctx, nativeSession, workspace, target, *decision)
		if decision.Verdict != "block" {
			continue
		}
		reasons := bounded(strings.Join(decision.Reasons, " "), 2048)
		if reasons == "" {
			reasons = "no reason given"
		}
		version := target.Version
		if version == "" {
			version = "(unpinned)"
		}
		message := fmt.Sprintf("meshAgent refused %s %s. %s", target.Name, version, reasons)
		if decision.Policy != "" {
			message += " Policy: " + bounded(decision.Policy, 1024) + "."
		}
		return ipc.PermissionResponse{Permission: "deny", UserMessage: message, AgentMessage: message + " Pick a different version or package."}
	}
	return allow
}

func (p *Processor) recordPolicy(ctx context.Context, nativeSession, workspace string, target installparse.Target, decision gate.Decision) {
	if nativeSession == "" {
		return
	}
	session, err := p.Store.Session(ctx, sessionKey(workspace, nativeSession))
	if err != nil || !session.Opened {
		return
	}
	advisories := make([]any, 0, min(len(decision.Advisories), 100))
	for index, advisory := range decision.Advisories {
		if index >= 100 {
			break
		}
		value := map[string]any{
			"id": bounded(advisory.ID, 256), "severity": advisory.Severity,
			"summary": bounded(advisory.Summary, 2048), "fixed_versions": boundedStrings(advisory.FixedVersions, 20, 128),
			"references": boundedStrings(advisory.References, 10, 2048),
		}
		if advisory.CWE != nil {
			value["cwe"] = bounded(*advisory.CWE, 128)
		}
		advisories = append(advisories, value)
	}
	var worst any
	if decision.Worst != nil {
		worst = *decision.Worst
	}
	var unavailable any
	if decision.Unavailable != nil {
		unavailable = *decision.Unavailable
	}
	draft := map[string]any{
		"type": "policy", "package": target.Name, "version": target.Version,
		"ecosystem": target.Ecosystem, "verdict": decision.Verdict,
		"reasons": boundedStrings(decision.Reasons, 32, 4096), "policy": bounded(decision.Policy, 4096), "worst": worst,
		"unavailable": boundedNullable(unavailable, 2048), "advisories": advisories,
	}
	if err := p.enqueueDrafts(ctx, nativeSession, workspace, []map[string]any{draft}); err == nil {
		p.notify()
	}
}

func (p *Processor) ensureOpening(ctx context.Context, nativeSession, repository, task string, draft map[string]any) (protocol.Envelope, error) {
	key := sessionKey(repository, nativeSession)
	seed := cryptoqueue.SessionSeed{Key: key, NativeSession: nativeSession, Repository: canonicalRepository(repository), Editor: adapter}
	return p.Store.EnsureOpening(ctx, seed, func(sessionID string) (protocol.Envelope, error) {
		eventID, err := protocol.NewEventID()
		if err != nil {
			return protocol.Envelope{}, err
		}
		body := protocol.SessionStart{
			ID: sessionID, SourceSessionID: bounded(nativeSession, 256), SourceEventID: bounded(stringDefault(draft["source_event_id"], stringDefault(draft["event_id"], eventID)), 256),
			Adapter: adapter, AdapterVersion: adapterVersion,
			Repository: protocol.Repository{ID: RepositoryID(repository), Name: bounded(repositoryName(repository), 256)},
			Task:       task, StartedAtMS: int64Default(draft["occurred_at_ms"], p.now().UnixMilli()), Sequence: 1,
		}
		return protocol.NewEnvelope("start", nativeSession, sessionID, "/api/v1/developer/sessions", body)
	})
}

func (p *Processor) enqueueDrafts(ctx context.Context, nativeSession, repository string, drafts []map[string]any) error {
	key := sessionKey(repository, nativeSession)
	session, err := p.Store.Session(ctx, key)
	if err != nil {
		return err
	}
	build := func(first int64) (protocol.Envelope, error) {
		events := make([]protocol.ActivityEvent, 0, len(drafts))
		for index, draft := range drafts {
			event, err := p.normalize(draft, first+int64(index))
			if err != nil {
				return protocol.Envelope{}, err
			}
			events = append(events, event)
		}
		body := protocol.ActivityBatch{Events: events}
		return protocol.NewEnvelope("events", nativeSession, session.DeveloperSessionID, "/api/v1/developer/sessions/"+session.DeveloperSessionID+"/events", body)
	}
	if len(drafts) == 1 && stringValue(drafts[0]["type"]) == "session" && boolValue(drafts[0]["ends"]) {
		_, err = p.Store.CloseWithActivity(ctx, key, build)
	} else {
		_, err = p.Store.EnqueueActivity(ctx, key, len(drafts), build)
	}
	return err
}

func (p *Processor) normalize(draft map[string]any, sequence int64) (protocol.ActivityEvent, error) {
	eventID := stringValue(draft["event_id"])
	if !validEventID.MatchString(eventID) || len([]rune(eventID)) > 124 {
		var err error
		eventID, err = protocol.NewEventID()
		if err != nil {
			return protocol.ActivityEvent{}, err
		}
	}
	sourceEventID := bounded(stringDefault(draft["source_event_id"], eventID), 256)
	if sourceEventID == "" {
		sourceEventID = eventID
	}
	occurredAt := int64Default(draft["occurred_at_ms"], p.now().UnixMilli())
	if occurredAt < 0 {
		occurredAt = p.now().UnixMilli()
	}
	event := protocol.ActivityEvent{EventID: eventID, SourceEventID: sourceEventID, Sequence: sequence, OccurredAtMS: occurredAt}
	switch stringValue(draft["type"]) {
	case "code":
		event.Type = "file.changed"
		event.Payload = map[string]any{"path": bounded(stringValue(draft["module"]), 1024), "operation": "update", "code": bounded(stringValue(draft["code"]), 200000), "because": boundedNullable(draft["because"], 256)}
	case "package":
		event.Type = "package.installed"
		event.Payload = map[string]any{"package": bounded(stringValue(draft["package"]), 256), "version": bounded(stringValue(draft["version"]), 128), "license": bounded(stringDefault(draft["license"], "unknown"), 256), "ecosystem": boundedNullable(draft["ecosystem"], 64), "command": boundedNullable(draft["command"], 4096)}
	case "policy":
		event.Type = "policy.evaluated"
		event.Payload = map[string]any{"package": bounded(stringValue(draft["package"]), 256), "version": bounded(stringValue(draft["version"]), 128), "ecosystem": stringDefault(draft["ecosystem"], "PyPI"), "verdict": stringDefault(draft["verdict"], "unknown"), "reasons": sliceDefault(draft["reasons"]), "policy": bounded(stringValue(draft["policy"]), 4096), "worst": boundedNullable(draft["worst"], 16), "unavailable": boundedNullable(draft["unavailable"], 2048), "advisories": sliceDefault(draft["advisories"])}
	case "tool":
		failed := boolValue(draft["failed"])
		if failed {
			event.Type = "tool.failed"
		} else {
			event.Type = "tool.completed"
		}
		event.Payload = map[string]any{"tool_name": bounded(stringValue(draft["name"]), 256), "detail": bounded(stringValue(draft["detail"]), 4096), "exit_code": nullableInt(draft["exit_code"])}
	case "decision":
		event.Type = "decision.recorded"
		event.Payload = map[string]any{"decision_id": bounded(stringValue(draft["id"]), 256), "statement": bounded(stringValue(draft["statement"]), 4096)}
	case "prompt":
		event.Type = "prompt.submitted"
		event.Payload = map[string]any{"prompt": bounded(stringValue(draft["prompt"]), 4096), "turn_id": boundedNullable(draft["turn_id"], 256)}
	case "response":
		event.Type = "response.completed"
		event.Payload = map[string]any{"turn_id": boundedNullable(draft["turn_id"], 256), "summary": bounded(stringValue(draft["summary"]), 4096), "stop_reason": boundedNullable(draft["stop_reason"], 128)}
	case "session":
		if !boolValue(draft["ends"]) {
			return protocol.ActivityEvent{}, errors.New("session opening is not an activity event")
		}
		event.Type = "session.ended"
		event.Payload = map[string]any{"reason": bounded(stringDefault(draft["reason"], "normal"), 256)}
	default:
		return protocol.ActivityEvent{}, errors.New("unsupported adapter event type")
	}
	return event, nil
}

func (p *Processor) translate(eventName string, event map[string]any, repository string, cfg repoConfig, nativeSession string) ([]map[string]any, error) {
	switch eventName {
	case "beforeSubmitPrompt":
		prompt := strings.TrimSpace(stringValue(event["prompt"]))
		if len([]rune(prompt)) > 4096 {
			prompt = string([]rune(prompt)[:4096])
		}
		if prompt == "" {
			return nil, nil
		}
		if session, err := p.Store.Session(context.Background(), sessionKey(repository, nativeSession)); err == nil && session.Opened {
			return []map[string]any{{"type": "prompt", "prompt": prompt, "turn_id": nullableString(event["generation_id"])}}, nil
		}
		return []map[string]any{{"type": "session", "agent": adapter, "task": prompt}}, nil
	case "afterFileEdit":
		filePath := stringValue(event["file_path"])
		if filePath == "" {
			return nil, nil
		}
		if !filepath.IsAbs(filePath) {
			filePath = filepath.Join(repository, filePath)
		}
		module, err := moduleName(filePath, repository)
		if err != nil || len(module) > 1024 || excluded(module, cfg.Exclude) {
			return nil, nil
		}
		code, err := readRegularFileNoFollow(filePath, p.Runtime.MaxFileBytes)
		if err != nil || !utf8.Valid(code) {
			return nil, nil
		}
		return []map[string]any{{"type": "code", "module": module, "code": string(code)}}, nil
	case "afterShellExecution":
		command := strings.TrimSpace(stringValue(event["command"]))
		if command == "" {
			return nil, nil
		}
		exit := event["exit_code"]
		if exit == nil {
			exit = event["exitCode"]
		}
		failed := exit != nil && int64Default(exit, -1) != 0
		capturedCommand := bounded(command, 4096)
		tool := map[string]any{"type": "tool", "name": "Shell", "detail": capturedCommand}
		if exit != nil {
			tool["failed"] = failed
			tool["exit_code"] = nullableInt(exit)
		}
		drafts := []map[string]any{tool}
		if !failed {
			for index, target := range installparse.Pinned(command) {
				if index >= 99 {
					break
				}
				drafts = append(drafts, map[string]any{"type": "package", "package": target.Name, "version": target.Version, "ecosystem": target.Ecosystem, "command": capturedCommand})
			}
		}
		return drafts, nil
	case "sessionEnd":
		session, err := p.Store.Session(context.Background(), sessionKey(repository, nativeSession))
		if err != nil || !session.Opened {
			return nil, nil
		}
		return []map[string]any{{"type": "session", "agent": adapter, "ends": true}}, nil
	default:
		return nil, nil
	}
}

func (p *Processor) notify() {
	if p.Notify != nil {
		p.Notify()
	}
}

func (p *Processor) now() time.Time {
	if p.Now != nil {
		return p.Now()
	}
	return time.Now()
}

func workspace(event map[string]any) string {
	if cwd := strings.TrimSpace(stringValue(event["cwd"])); cwd != "" {
		return canonicalRepository(cwd)
	}
	if roots, ok := event["workspace_roots"].([]any); ok && len(roots) > 0 {
		return canonicalRepository(stringValue(roots[0]))
	}
	if roots, ok := event["workspace_roots"].([]string); ok && len(roots) > 0 {
		return canonicalRepository(roots[0])
	}
	cwd, _ := os.Getwd()
	return canonicalRepository(cwd)
}

func readRepoConfig(repository string) (repoConfig, bool) {
	body, err := os.ReadFile(filepath.Join(repository, ".meshagent.json"))
	if err != nil {
		return repoConfig{}, false
	}
	var raw map[string]any
	if json.Unmarshal(body, &raw) != nil {
		return repoConfig{}, false
	}
	record, ok := raw["record"].(bool)
	if !ok || !record {
		return repoConfig{}, false
	}
	var cfg repoConfig
	if json.Unmarshal(body, &cfg) != nil {
		return repoConfig{}, false
	}
	return cfg, true
}

func moduleName(filePath, repository string) (string, error) {
	if !filepath.IsAbs(filePath) {
		filePath = filepath.Join(repository, filePath)
	}
	absFile, err := filepath.Abs(filePath)
	if err != nil {
		return "", err
	}
	if evaluated, err := filepath.EvalSymlinks(absFile); err == nil {
		absFile = evaluated
	}
	root := canonicalRepository(repository)
	rel, err := filepath.Rel(root, absFile)
	if err != nil {
		return "", err
	}
	module := filepath.ToSlash(rel)
	if module == "." || strings.HasPrefix(module, "../") || strings.HasPrefix(module, "/") || strings.Contains(module, "/../") {
		return "", errors.New("file is outside repository")
	}
	return module, nil
}

func excluded(module string, patterns []string) bool {
	name := filepath.Base(module)
	for _, pattern := range patterns {
		if wildcardMatch(pattern, module) {
			return true
		}
		if wildcardMatch(pattern, name) {
			return true
		}
	}
	return false
}

func wildcardMatch(pattern, value string) bool {
	if runtime.GOOS == "windows" {
		pattern = strings.ToLower(pattern)
		value = strings.ToLower(value)
	}
	var expression strings.Builder
	expression.WriteString("^")
	runes := []rune(pattern)
	for index := 0; index < len(runes); index++ {
		switch runes[index] {
		case '*':
			expression.WriteString(".*")
		case '?':
			expression.WriteString(".")
		case '[':
			end := index + 1
			if end < len(runes) && (runes[end] == '!' || runes[end] == '^') {
				end++
			}
			if end < len(runes) && runes[end] == ']' {
				end++
			}
			for end < len(runes) && runes[end] != ']' {
				end++
			}
			if end >= len(runes) {
				expression.WriteString(`\[`)
				continue
			}
			class := string(runes[index+1 : end])
			if strings.HasPrefix(class, "!") {
				class = "^" + class[1:]
			}
			expression.WriteString("[")
			expression.WriteString(class)
			expression.WriteString("]")
			index = end
		default:
			expression.WriteString(regexp.QuoteMeta(string(runes[index])))
		}
	}
	expression.WriteString("$")
	matched, err := regexp.MatchString(expression.String(), value)
	return err == nil && matched
}

func conversation(event map[string]any) string {
	return stringDefault(event["conversation_id"], stringValue(event["session_id"]))
}
func canonicalRepository(value string) string {
	absolute, err := filepath.Abs(value)
	if err != nil {
		return value
	}
	if evaluated, err := filepath.EvalSymlinks(absolute); err == nil {
		return evaluated
	}
	return filepath.Clean(absolute)
}
func repositoryName(value string) string {
	name := filepath.Base(canonicalRepository(value))
	if name == "." || name == string(filepath.Separator) || name == "" {
		return "repository"
	}
	return name
}
func RepositoryID(value string) string {
	root := canonicalRepository(value)
	text := strings.Trim(unsafeComponent.ReplaceAllString(strings.TrimSpace(root), "-"), ".-")
	if len(text) > 48 {
		text = text[:48]
	}
	if text == "" {
		text = "unknown"
	}
	sum := sha256.Sum256([]byte(root))
	return "repo-" + text + "-" + hex.EncodeToString(sum[:])[:12]
}
func sessionKey(repository, native string) string {
	sum := sha256.Sum256([]byte(RepositoryID(repository) + "\x1f" + adapter + "\x1f" + native))
	return hex.EncodeToString(sum[:])
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprint(value)
}
func stringDefault(value any, fallback string) string {
	if found := stringValue(value); found != "" {
		return found
	}
	return fallback
}
func bounded(value string, limit int) string {
	runes := []rune(value)
	if len(runes) > limit {
		return string(runes[:limit])
	}
	return value
}
func boundedNullable(value any, limit int) any {
	if text := bounded(stringValue(value), limit); text != "" {
		return text
	}
	return nil
}
func boundedStrings(values []string, maxItems, maxRunes int) []string {
	if len(values) > maxItems {
		values = values[:maxItems]
	}
	boundedValues := make([]string, 0, len(values))
	for _, value := range values {
		boundedValues = append(boundedValues, bounded(value, maxRunes))
	}
	return boundedValues
}
func boolValue(value any) bool { found, _ := value.(bool); return found }
func nullableString(value any) any {
	if text := stringValue(value); text != "" {
		return text
	}
	return nil
}
func int64Default(value any, fallback int64) int64 {
	switch found := value.(type) {
	case json.Number:
		parsed, err := found.Int64()
		if err == nil {
			return parsed
		}
	case float64:
		return int64(found)
	case int:
		return int64(found)
	case int64:
		return found
	case string:
		parsed, err := strconv.ParseInt(found, 10, 64)
		if err == nil {
			return parsed
		}
	}
	return fallback
}
func nullableInt(value any) any {
	parsed := int64Default(value, -1)
	if parsed < 0 {
		return nil
	}
	return parsed
}
func sliceDefault(value any) any {
	if value == nil {
		return []any{}
	}
	return value
}
