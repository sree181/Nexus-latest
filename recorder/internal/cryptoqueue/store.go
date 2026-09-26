package cryptoqueue

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"

	"github.com/sree181/Nexus-latest/recorder/internal/canonicaljson"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

var ErrQueueFull = errors.New("local recording queue is full; accepted events were preserved and this observation was not recorded")
var ErrSessionClosed = errors.New("local recording session is already closed")

const blockedUntilMS = int64(^uint64(0) >> 1)

type Limits struct {
	MaxBatches int
	MaxBytes   int64
}

type SessionSeed struct {
	Key           string
	NativeSession string
	Repository    string
	Editor        string
	SessionID     string
}

type Session struct {
	Key                 string
	NativeSession       string
	Repository          string
	Editor              string
	DeveloperSessionID  string
	NextSequence        int64
	OpeningAcknowledged bool
	Opened              bool
	Closed              bool
	RunID               string
	LastServerSequence  int64
	QueueRejectedEvents int64
	LastQueueErrorAtMS  int64
	LastQueueError      string
}

type Record struct {
	ID            int64
	SessionKey    string
	Envelope      protocol.Envelope
	SequenceStart int64
	SequenceEnd   int64
	Attempts      int
	LeaseID       string
}

type Summary struct {
	Sessions              int64  `json:"sessions"`
	QueuedBatches         int64  `json:"queued_batches"`
	QueuedBytes           int64  `json:"queued_bytes"`
	BlockedBatches        int64  `json:"blocked_batches"`
	BackpressuredSessions int64  `json:"backpressured_sessions"`
	RejectedEvents        int64  `json:"queue_rejected_events"`
	OldestCreatedAtMS     *int64 `json:"oldest_created_at_ms,omitempty"`
}

type Store struct {
	db     *sql.DB
	box    *cipherBox
	limits Limits
	mu     sync.Mutex
	now    func() time.Time
}

func Open(path string, key []byte, limits Limits) (*Store, error) {
	if limits.MaxBatches < 1 || limits.MaxBytes < 1 {
		return nil, errors.New("queue limits must be positive")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	box, err := newCipherBox(key)
	if err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	store := &Store{db: db, box: box, limits: limits, now: time.Now}
	if err := store.initialize(context.Background()); err != nil {
		_ = db.Close()
		return nil, err
	}
	if err := os.Chmod(path, 0o600); err != nil && !errors.Is(err, os.ErrPermission) {
		_ = db.Close()
		return nil, err
	}
	return store, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) initialize(ctx context.Context) error {
	statements := []string{
		`PRAGMA journal_mode=WAL`,
		`PRAGMA synchronous=FULL`,
		`PRAGMA foreign_keys=ON`,
		`PRAGMA busy_timeout=5000`,
		`CREATE TABLE IF NOT EXISTS sessions (
			session_key TEXT PRIMARY KEY,
			native_session TEXT NOT NULL,
			repository TEXT NOT NULL,
			editor TEXT NOT NULL,
			developer_session_id TEXT NOT NULL UNIQUE,
			next_sequence INTEGER NOT NULL DEFAULT 2,
			opening_ciphertext BLOB,
			opening_nonce BLOB,
			opening_aad TEXT,
			opening_sha256 TEXT,
			opening_acknowledged INTEGER NOT NULL DEFAULT 0,
			opened INTEGER NOT NULL DEFAULT 0,
			closed INTEGER NOT NULL DEFAULT 0,
			run_id TEXT NOT NULL DEFAULT '',
			last_server_sequence INTEGER NOT NULL DEFAULT 0,
			queue_rejected_events INTEGER NOT NULL DEFAULT 0,
			queue_error_at_ms INTEGER NOT NULL DEFAULT 0,
			queue_error TEXT NOT NULL DEFAULT '',
			created_at_ms INTEGER NOT NULL,
			updated_at_ms INTEGER NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS queue_records (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
			kind TEXT NOT NULL CHECK (kind IN ('start','events')),
			sequence_start INTEGER NOT NULL,
			sequence_end INTEGER NOT NULL,
			payload_ciphertext BLOB NOT NULL,
			nonce BLOB NOT NULL,
			aad TEXT NOT NULL,
			payload_sha256 TEXT NOT NULL,
			plaintext_bytes INTEGER NOT NULL,
			state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased')),
			lease_id TEXT,
			lease_expires_at_ms INTEGER,
			attempts INTEGER NOT NULL DEFAULT 0,
			next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,
			created_at_ms INTEGER NOT NULL,
			UNIQUE(session_key, kind, sequence_start, sequence_end)
		)`,
		`CREATE INDEX IF NOT EXISTS ix_queue_ready ON queue_records(session_key, state, next_attempt_at_ms, sequence_start, id)`,
		`CREATE TABLE IF NOT EXISTS recorder_state (
			id INTEGER PRIMARY KEY CHECK (id=1),
			rejected_opening_events INTEGER NOT NULL DEFAULT 0,
			last_queue_error_at_ms INTEGER NOT NULL DEFAULT 0,
			last_queue_error TEXT NOT NULL DEFAULT ''
		)`,
		`INSERT OR IGNORE INTO recorder_state (id) VALUES (1)`,
	}
	for _, statement := range statements {
		if _, err := s.db.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	if err := s.ensureClosedColumn(ctx); err != nil {
		return err
	}
	return nil
}

func (s *Store) ensureClosedColumn(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, `PRAGMA table_info(sessions)`)
	if err != nil {
		return err
	}
	defer rows.Close()
	found := false
	for rows.Next() {
		var cid, notNull, primaryKey int
		var name, columnType string
		var defaultValue any
		if err := rows.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			return err
		}
		if name == "closed" {
			found = true
		}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	if found {
		return nil
	}
	_, err = s.db.ExecContext(ctx, `ALTER TABLE sessions ADD COLUMN closed INTEGER NOT NULL DEFAULT 0`)
	return err
}

func (s *Store) EnsureOpening(ctx context.Context, seed SessionSeed, build func(sessionID string) (protocol.Envelope, error)) (protocol.Envelope, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return protocol.Envelope{}, err
	}
	defer tx.Rollback()

	session, err := s.sessionTx(ctx, tx, seed.Key)
	if err == nil {
		opening, decodeErr := s.openingEnvelope(ctx, session.Key, tx)
		if decodeErr != nil {
			return protocol.Envelope{}, decodeErr
		}
		if !session.OpeningAcknowledged {
			if err := s.ensureQueuedTx(ctx, tx, session.Key, opening, 1, 1); err != nil {
				if errors.Is(err, ErrQueueFull) {
					_ = tx.Rollback()
					if countErr := s.recordRejectedOpening(ctx); countErr != nil {
						return protocol.Envelope{}, countErr
					}
				}
				return protocol.Envelope{}, err
			}
		}
		if err := tx.Commit(); err != nil {
			return protocol.Envelope{}, err
		}
		return opening, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return protocol.Envelope{}, err
	}
	if seed.SessionID == "" {
		seed.SessionID, err = protocol.NewSessionID()
		if err != nil {
			return protocol.Envelope{}, err
		}
	}
	opening, err := build(seed.SessionID)
	if err != nil {
		return protocol.Envelope{}, err
	}
	encoded, err := canonicaljson.Marshal(opening)
	if err != nil {
		return protocol.Envelope{}, err
	}
	aad := "session-opening|" + seed.Key
	ciphertext, nonce, digest, err := s.box.seal(encoded, aad)
	if err != nil {
		return protocol.Envelope{}, err
	}
	now := s.now().UnixMilli()
	_, err = tx.ExecContext(ctx, `INSERT INTO sessions (
		session_key,native_session,repository,editor,developer_session_id,next_sequence,
		opening_ciphertext,opening_nonce,opening_aad,opening_sha256,opened,created_at_ms,updated_at_ms
	) VALUES (?,?,?,?,?,2,?,?,?,?,1,?,?)`,
		seed.Key, seed.NativeSession, seed.Repository, seed.Editor, seed.SessionID,
		ciphertext, nonce, aad, digest, now, now,
	)
	if err != nil {
		return protocol.Envelope{}, err
	}
	if err := s.ensureQueuedTx(ctx, tx, seed.Key, opening, 1, 1); err != nil {
		if errors.Is(err, ErrQueueFull) {
			_ = tx.Rollback()
			if countErr := s.recordRejectedOpening(ctx); countErr != nil {
				return protocol.Envelope{}, countErr
			}
		}
		return protocol.Envelope{}, err
	}
	if err := tx.Commit(); err != nil {
		return protocol.Envelope{}, err
	}
	return opening, nil
}

func (s *Store) EnqueueActivity(ctx context.Context, sessionKey string, eventCount int, build func(firstSequence int64) (protocol.Envelope, error)) (protocol.Envelope, error) {
	return s.enqueueActivity(ctx, sessionKey, eventCount, false, build)
}

func (s *Store) CloseWithActivity(ctx context.Context, sessionKey string, build func(firstSequence int64) (protocol.Envelope, error)) (protocol.Envelope, error) {
	return s.enqueueActivity(ctx, sessionKey, 1, true, build)
}

func (s *Store) enqueueActivity(ctx context.Context, sessionKey string, eventCount int, closes bool, build func(firstSequence int64) (protocol.Envelope, error)) (protocol.Envelope, error) {
	if eventCount < 1 || eventCount > 100 {
		return protocol.Envelope{}, errors.New("event count must be between 1 and 100")
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return protocol.Envelope{}, err
	}
	defer tx.Rollback()
	session, err := s.sessionTx(ctx, tx, sessionKey)
	if err != nil {
		return protocol.Envelope{}, err
	}
	if session.Closed {
		return protocol.Envelope{}, ErrSessionClosed
	}
	first := session.NextSequence
	envelope, err := build(first)
	if err != nil {
		return protocol.Envelope{}, err
	}
	last := first + int64(eventCount) - 1
	if err := s.ensureQueuedTx(ctx, tx, sessionKey, envelope, first, last); err != nil {
		if errors.Is(err, ErrQueueFull) {
			now := s.now().UnixMilli()
			_, updateErr := tx.ExecContext(ctx, `UPDATE sessions SET
				queue_rejected_events=queue_rejected_events+?, queue_error_at_ms=?, queue_error=?, updated_at_ms=?
				WHERE session_key=?`, eventCount, now, ErrQueueFull.Error(), now, sessionKey)
			if updateErr != nil {
				return protocol.Envelope{}, updateErr
			}
			if commitErr := tx.Commit(); commitErr != nil {
				return protocol.Envelope{}, commitErr
			}
		}
		return protocol.Envelope{}, err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE sessions SET next_sequence=?,closed=CASE WHEN ? THEN 1 ELSE closed END,updated_at_ms=? WHERE session_key=?`, last+1, closes, s.now().UnixMilli(), sessionKey); err != nil {
		return protocol.Envelope{}, err
	}
	if err := tx.Commit(); err != nil {
		return protocol.Envelope{}, err
	}
	return envelope, nil
}

func (s *Store) EnsureOpeningQueued(ctx context.Context, sessionKey string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	session, err := s.sessionTx(ctx, tx, sessionKey)
	if err != nil {
		return err
	}
	if session.OpeningAcknowledged {
		return tx.Commit()
	}
	opening, err := s.openingEnvelope(ctx, sessionKey, tx)
	if err != nil {
		return err
	}
	if err := s.ensureQueuedTx(ctx, tx, sessionKey, opening, 1, 1); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) LeaseNext(ctx context.Context, sessionKey string, duration time.Duration) (*Record, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	now := s.now().UnixMilli()
	row := tx.QueryRowContext(ctx, `SELECT id,kind,sequence_start,sequence_end,payload_ciphertext,nonce,aad,payload_sha256,attempts
		FROM queue_records WHERE id=(
			SELECT id FROM queue_records WHERE session_key=? ORDER BY sequence_start,id LIMIT 1
		) AND next_attempt_at_ms<=? AND (state='pending' OR (state='leased' AND lease_expires_at_ms<=?))`, sessionKey, now, now)
	var record Record
	var kind, aad, digest string
	var ciphertext, nonce []byte
	if err := row.Scan(&record.ID, &kind, &record.SequenceStart, &record.SequenceEnd, &ciphertext, &nonce, &aad, &digest, &record.Attempts); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	leaseID, err := randomID()
	if err != nil {
		return nil, err
	}
	result, err := tx.ExecContext(ctx, `UPDATE queue_records SET state='leased',lease_id=?,lease_expires_at_ms=?,attempts=attempts+1 WHERE id=?`, leaseID, now+duration.Milliseconds(), record.ID)
	if err != nil {
		return nil, err
	}
	if affected, _ := result.RowsAffected(); affected != 1 {
		return nil, errors.New("failed to claim queue record")
	}
	plaintext, err := s.box.open(ciphertext, nonce, aad, digest)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(plaintext, &record.Envelope); err != nil {
		return nil, fmt.Errorf("decode queue record: %w", err)
	}
	record.SessionKey = sessionKey
	record.LeaseID = leaseID
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return &record, nil
}

func (s *Store) Acknowledge(ctx context.Context, record Record, response map[string]any) error {
	if !protocol.Acknowledged(record.Envelope, response) {
		return errors.New("response does not acknowledge queue record")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `DELETE FROM queue_records WHERE id=? AND state='leased' AND lease_id=?`, record.ID, record.LeaseID)
	if err != nil {
		return err
	}
	if affected, _ := result.RowsAffected(); affected != 1 {
		return errors.New("queue lease no longer belongs to sender")
	}
	runID, _ := response["run_id"].(string)
	ack := record.SequenceEnd
	opened := 0
	openingAck := 0
	if record.Envelope.Kind == "start" {
		opened = 1
		openingAck = 1
	}
	_, err = tx.ExecContext(ctx, `UPDATE sessions SET
		run_id=CASE WHEN ?<>'' THEN ? ELSE run_id END,
		last_server_sequence=CASE WHEN ?>last_server_sequence THEN ? ELSE last_server_sequence END,
		next_sequence=CASE WHEN ?+1>next_sequence THEN ?+1 ELSE next_sequence END,
		opened=CASE WHEN ?=1 THEN 1 ELSE opened END,
		opening_acknowledged=CASE WHEN ?=1 THEN 1 ELSE opening_acknowledged END,
		updated_at_ms=? WHERE session_key=?`,
		runID, runID, ack, ack, ack, ack, opened, openingAck, s.now().UnixMilli(), record.SessionKey,
	)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) Release(ctx context.Context, record Record, retryAfter time.Duration) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	result, err := s.db.ExecContext(ctx, `UPDATE queue_records SET state='pending',lease_id=NULL,lease_expires_at_ms=NULL,next_attempt_at_ms=? WHERE id=? AND lease_id=?`, s.now().Add(retryAfter).UnixMilli(), record.ID, record.LeaseID)
	if err != nil {
		return err
	}
	if affected, _ := result.RowsAffected(); affected != 1 {
		return errors.New("queue lease no longer belongs to sender")
	}
	return nil
}

func (s *Store) Block(ctx context.Context, record Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	result, err := s.db.ExecContext(ctx, `UPDATE queue_records SET state='pending',lease_id=NULL,lease_expires_at_ms=NULL,next_attempt_at_ms=? WHERE id=? AND lease_id=?`, blockedUntilMS, record.ID, record.LeaseID)
	if err != nil {
		return err
	}
	if affected, _ := result.RowsAffected(); affected != 1 {
		return errors.New("queue lease no longer belongs to sender")
	}
	return nil
}

func (s *Store) UnblockAll(ctx context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := s.db.ExecContext(ctx, `UPDATE queue_records SET next_attempt_at_ms=0 WHERE next_attempt_at_ms=?`, blockedUntilMS)
	return err
}

func (s *Store) Session(ctx context.Context, key string) (Session, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sessionTx(ctx, s.db, key)
}

func (s *Store) ReadySessions(ctx context.Context) ([]string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now().UnixMilli()
	rows, err := s.db.QueryContext(ctx, `SELECT head.session_key FROM queue_records AS head
		WHERE head.id=(SELECT id FROM queue_records WHERE session_key=head.session_key ORDER BY sequence_start,id LIMIT 1)
		AND head.next_attempt_at_ms<=? AND (head.state='pending' OR (head.state='leased' AND head.lease_expires_at_ms<=?))
		ORDER BY head.created_at_ms,head.session_key`, now, now)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var keys []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return nil, err
		}
		keys = append(keys, key)
	}
	return keys, rows.Err()
}

func (s *Store) Summary(ctx context.Context) (Summary, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out Summary
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*),COALESCE(SUM(queue_rejected_events),0),COALESCE(SUM(CASE WHEN queue_rejected_events>0 THEN 1 ELSE 0 END),0) FROM sessions`).Scan(&out.Sessions, &out.RejectedEvents, &out.BackpressuredSessions); err != nil {
		return out, err
	}
	var rejectedOpenings int64
	if err := s.db.QueryRowContext(ctx, `SELECT rejected_opening_events FROM recorder_state WHERE id=1`).Scan(&rejectedOpenings); err != nil {
		return out, err
	}
	out.RejectedEvents += rejectedOpenings
	var oldest sql.NullInt64
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*),COALESCE(SUM(plaintext_bytes),0),COALESCE(SUM(CASE WHEN next_attempt_at_ms=? THEN 1 ELSE 0 END),0),MIN(created_at_ms) FROM queue_records`, blockedUntilMS).Scan(&out.QueuedBatches, &out.QueuedBytes, &out.BlockedBatches, &oldest); err != nil {
		return out, err
	}
	if oldest.Valid {
		out.OldestCreatedAtMS = &oldest.Int64
	}
	return out, nil
}

func (s *Store) recordRejectedOpening(ctx context.Context) error {
	now := s.now().UnixMilli()
	_, err := s.db.ExecContext(ctx, `UPDATE recorder_state SET rejected_opening_events=rejected_opening_events+1,last_queue_error_at_ms=?,last_queue_error=? WHERE id=1`, now, ErrQueueFull.Error())
	return err
}

func (s *Store) ensureQueuedTx(ctx context.Context, tx *sql.Tx, sessionKey string, envelope protocol.Envelope, first, last int64) error {
	var existing int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM queue_records WHERE session_key=? AND kind=? AND sequence_start=? AND sequence_end=?`, sessionKey, envelope.Kind, first, last).Scan(&existing); err != nil {
		return err
	}
	if existing > 0 {
		return nil
	}
	encoded, err := canonicaljson.Marshal(envelope)
	if err != nil {
		return err
	}
	var count int
	var bytes int64
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*),COALESCE(SUM(plaintext_bytes),0) FROM queue_records`).Scan(&count, &bytes); err != nil {
		return err
	}
	if count+1 > s.limits.MaxBatches || bytes+int64(len(encoded)) > s.limits.MaxBytes {
		return ErrQueueFull
	}
	aad := fmt.Sprintf("queue|%s|%s|%d|%d", sessionKey, envelope.Kind, first, last)
	ciphertext, nonce, digest, err := s.box.seal(encoded, aad)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO queue_records (
		session_key,kind,sequence_start,sequence_end,payload_ciphertext,nonce,aad,payload_sha256,plaintext_bytes,created_at_ms
	) VALUES (?,?,?,?,?,?,?,?,?,?)`, sessionKey, envelope.Kind, first, last, ciphertext, nonce, aad, digest, len(encoded), s.now().UnixMilli())
	return err
}

func (s *Store) openingEnvelope(ctx context.Context, sessionKey string, query interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}) (protocol.Envelope, error) {
	var ciphertext, nonce []byte
	var aad, digest string
	if err := query.QueryRowContext(ctx, `SELECT opening_ciphertext,opening_nonce,opening_aad,opening_sha256 FROM sessions WHERE session_key=?`, sessionKey).Scan(&ciphertext, &nonce, &aad, &digest); err != nil {
		return protocol.Envelope{}, err
	}
	plaintext, err := s.box.open(ciphertext, nonce, aad, digest)
	if err != nil {
		return protocol.Envelope{}, err
	}
	var envelope protocol.Envelope
	if err := json.Unmarshal(plaintext, &envelope); err != nil {
		return protocol.Envelope{}, err
	}
	return envelope, nil
}

type rowQueryer interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func (s *Store) sessionTx(ctx context.Context, query rowQueryer, key string) (Session, error) {
	var session Session
	var openingAck, opened, closed int
	err := query.QueryRowContext(ctx, `SELECT session_key,native_session,repository,editor,developer_session_id,next_sequence,opening_acknowledged,opened,closed,run_id,last_server_sequence,queue_rejected_events,queue_error_at_ms,queue_error FROM sessions WHERE session_key=?`, key).Scan(
		&session.Key, &session.NativeSession, &session.Repository, &session.Editor, &session.DeveloperSessionID,
		&session.NextSequence, &openingAck, &opened, &closed, &session.RunID,
		&session.LastServerSequence, &session.QueueRejectedEvents, &session.LastQueueErrorAtMS, &session.LastQueueError,
	)
	session.OpeningAcknowledged = openingAck == 1
	session.Opened = opened == 1
	session.Closed = closed == 1
	return session, err
}

func randomID() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return hex.EncodeToString(value), nil
}
