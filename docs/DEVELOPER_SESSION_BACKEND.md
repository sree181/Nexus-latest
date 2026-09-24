# Developer Session Backend and Adapter Contract

**Status:** Implemented on `manus/developer-sessions-v1`

**Author:** Manus AI

## 1. Product boundary

MeshAgent is **not a coding editor and not a replacement for Cursor or Claude Code**. A developer continues to work in the editor they already use. A small MeshAgent adapter observes approved lifecycle events, sends them to the customer-operated MeshAgent API, and asks MeshAgent for a policy decision before a dependency-install command runs.

The API commits each observed event to a durable activity ledger before it projects evidence into HyperMesh. This separation matters. The activity ledger answers what the adapter delivered, in what order, and whether it was projected. HyperMesh answers what governed evidence and relationships exist as a result.[1] [2]

## 2. Developer workflow

A production session follows this sequence:

1. The first editor prompt opens a MeshAgent Developer session. The adapter creates one opaque `ses_...` identifier and persists it locally.
2. The API authenticates the developer or paired device. It binds the session to that identity, adapter, source conversation, and repository identifier.
3. Each editor hook reserves the next monotonically increasing sequence number before attempting the network call.
4. The adapter appends the outbound record to a private local queue. It sends the oldest unacknowledged record first. If the queue is full, it preserves the complete accepted prefix, rejects the new observation locally, rolls back its sequence reservation, and exposes the backpressure through `meshagent status` and `meshagent doctor`.
5. The API commits the batch to SQLite in one transaction. Unique constraints make exact retries safe and reject divergent reuse.
6. The API projects committed events into HyperMesh in sequence. Projection state is visible independently from delivery state, and a lifespan worker retries transient failures with capped exponential backoff.
7. A package pre-tool hook calls the synchronous package gate. The resulting `allow`, `warn`, `block`, or `unknown` decision is immediately recorded as a `policy.evaluated` activity event.
8. A session-end hook records the terminal event. The API refuses new late events, but exact retries remain idempotent.

The adapter uses short authenticated HTTP requests rather than a permanent editor-to-server socket. This is intentional. Editor hooks are brief operating-system processes. Durable local queuing plus ordered replay is more reliable than expecting a long-lived connection to survive editor restarts, laptop sleep, network changes, and API deployment restarts.[3] [4]

## 3. Implemented API

| Method | Path                                                         | Purpose                                                     | Caller                                          |
| ------ | ------------------------------------------------------------ | ----------------------------------------------------------- | ----------------------------------------------- |
| `POST` | `/api/v1/developer/sessions`                                 | Open or exactly replay a session                            | Paired recording device or authenticated person |
| `GET`  | `/api/v1/developer/sessions`                                 | List the caller's sessions                                  | Session owner                                   |
| `GET`  | `/api/v1/developer/sessions/{session_id}`                    | Read one owned session                                      | Session owner                                   |
| `POST` | `/api/v1/developer/sessions/{session_id}/events`             | Commit and project an ordered event batch                   | Session owner or their paired recorder          |
| `GET`  | `/api/v1/developer/sessions/{session_id}/events`             | Read ordered activity and projection state                  | Session owner                                   |
| `GET`  | `/api/v1/developer/sessions/{session_id}/policy-evaluations` | Read policy decisions attached to the session               | Session owner                                   |
| `POST` | `/api/gate/package`                                          | Obtain the synchronous package decision used before install | Paired recording device or authenticated person |

All session reads are owner scoped. Looking up another developer's session returns `404`, which does not reveal whether the identifier exists. Event sequence gaps return `409` with `expected_sequence` and `received_sequence`. Reusing an event or session identity with different immutable content also returns `409`.[1] [5]

## 4. Exact database schema

The schema below is the effective schema after migrations `001` and `002`, not a conceptual model.[6] [8]

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS developer_sessions (
    id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    adapter TEXT NOT NULL CHECK (adapter IN ('cursor', 'claude-code')),
    adapter_version TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    projection_session_key TEXT,
    repository_id TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    repository_remote TEXT,
    repository_branch TEXT,
    repository_commit TEXT,
    task TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('starting', 'active', 'ending', 'completed', 'failed')),
    started_at_ms INTEGER NOT NULL,
    last_seen_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER,
    next_sequence INTEGER NOT NULL DEFAULT 2 CHECK (next_sequence >= 2),
    last_acked_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_acked_sequence >= 0),
    end_sequence INTEGER,
    run_id TEXT UNIQUE,
    device_id TEXT,
    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
    failure_reason TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (owner_subject, adapter, repository_id, source_session_id)
);

CREATE INDEX IF NOT EXISTS ix_developer_sessions_owner_updated
    ON developer_sessions(owner_subject, updated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_developer_sessions_repository
    ON developer_sessions(repository_id, updated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_developer_sessions_status
    ON developer_sessions(status, updated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_developer_sessions_projection_key
    ON developer_sessions(owner_subject, projection_session_key);

CREATE TABLE IF NOT EXISTS activity_events (
    event_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES developer_sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'session.started', 'prompt.submitted', 'tool.started', 'tool.completed',
        'tool.failed', 'file.changed', 'decision.recorded', 'package.requested',
        'package.installed', 'policy.evaluated', 'response.completed', 'session.ended'
    )),
    occurred_at_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    projection_status TEXT NOT NULL DEFAULT 'pending' CHECK (projection_status IN (
        'pending', 'projecting', 'projected', 'refused', 'failed'
    )),
    projection_attempts INTEGER NOT NULL DEFAULT 0,
    projection_last_attempt_at_ms INTEGER,
    projection_next_attempt_at_ms INTEGER,
    projected_at_ms INTEGER,
    projection_error TEXT,
    run_id TEXT,
    created_at_ms INTEGER NOT NULL,
    UNIQUE (session_id, sequence),
    UNIQUE (session_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS ix_activity_events_session_sequence
    ON activity_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS ix_activity_events_projection
    ON activity_events(projection_status, received_at_ms);
CREATE INDEX IF NOT EXISTS ix_activity_events_projection_retry
    ON activity_events(projection_status, projection_next_attempt_at_ms, received_at_ms);
CREATE INDEX IF NOT EXISTS ix_activity_events_type
    ON activity_events(event_type, received_at_ms DESC);

CREATE TABLE IF NOT EXISTS policy_evaluations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES developer_sessions(id) ON DELETE CASCADE,
    activity_event_id TEXT NOT NULL UNIQUE REFERENCES activity_events(event_id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    version TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('allow', 'warn', 'block', 'unknown')),
    reasons_json TEXT NOT NULL,
    advisories_json TEXT NOT NULL,
    worst TEXT,
    unavailable TEXT,
    policy TEXT NOT NULL,
    evaluated_at_ms INTEGER NOT NULL,
    owner_subject TEXT NOT NULL,
    device_id TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_policy_evaluations_session_time
    ON policy_evaluations(session_id, evaluated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_policy_evaluations_verdict_time
    ON policy_evaluations(verdict, evaluated_at_ms DESC);
```

### Why there are three tables

`developer_sessions` is the current session summary and ownership boundary. `activity_events` is the ordered, replay-safe operational ledger. `policy_evaluations` is a query-optimized projection of policy events; its foreign key ensures every policy decision still has an exact activity-event source.

The `activity_events` row is committed before HyperMesh projection. Delivery and projection therefore have separate states. An event can be safely received even if the engine is temporarily unavailable. The API retries failed work automatically with capped exponential backoff and resets interrupted `projecting` work on startup.[1] [2]

New sessions use their opaque ledger ID as `projection_session_key`, so native editor identifiers cannot merge runs across repositories or adapters. Migration `002` preserves the legacy source-session key only for an existing row already bound to a HyperMesh run; this prevents an upgrade from splitting previously recorded evidence.[8]

## 5. Exact wire models

### Session start request

```json
{
  "id": "ses_16e59e17bae32e03af7c4c4c0fa767b7",
  "source_session_id": "cursor-conversation-42",
  "source_event_id": "cursor-session-start-42",
  "adapter": "cursor",
  "adapter_version": "1.0.0",
  "repository": {
    "id": "repo-payments-123",
    "name": "payments-api",
    "remote": null,
    "branch": "feature/retries",
    "commit": "abc123"
  },
  "task": "Add retry-safe payment capture",
  "started_at_ms": 1790207456504,
  "sequence": 1
}
```

`id` must begin with `ses_` and contain at least 16 opaque characters. The adapter generates it with cryptographically random bytes and persists it once. `source_session_id` is the editor's conversation identifier. `repository.id` is a stable local hash; MeshAgent does not need to transmit an absolute workstation path.

### Activity batch request

```json
{
  "events": [
    {
      "event_id": "evt_1111111111111111",
      "source_event_id": "cursor-file-1",
      "sequence": 2,
      "occurred_at_ms": 1790207457000,
      "type": "file.changed",
      "payload": {
        "path": "src/payments.py",
        "operation": "update",
        "code": "def capture():\n    return 'ok'\n",
        "because": null
      }
    }
  ]
}
```

The accepted activity discriminators and payloads are:

| Event type           | Required payload                                                          | Projection behavior                     |
| -------------------- | ------------------------------------------------------------------------- | --------------------------------------- |
| `prompt.submitted`   | `prompt`, optional `turn_id`                                              | Activity ledger only                    |
| `tool.started`       | `tool_name`, optional call identifier and detail                          | Activity ledger only                    |
| `tool.completed`     | `tool_name`, detail, optional exit code                                   | HyperMesh tool evidence                 |
| `tool.failed`        | `tool_name`, detail, optional exit code                                   | HyperMesh tool evidence marked failed   |
| `file.changed`       | relative `path`, operation, content for create/update, optional `because` | HyperMesh code evidence                 |
| `decision.recorded`  | `decision_id`, `statement`                                                | HyperMesh decision evidence             |
| `package.requested`  | package coordinates and optional command                                  | Activity ledger only                    |
| `package.installed`  | package, version, license, optional ecosystem                             | HyperMesh package evidence              |
| `policy.evaluated`   | package, version, verdict, reasons, policy, advisory details              | Policy table and activity ledger        |
| `response.completed` | optional turn and summary                                                 | Activity ledger only                    |
| `session.ended`      | reason                                                                    | Completes the session and HyperMesh run |

### Ingestion receipt

```json
{
  "session": {
    "id": "ses_16e59e17bae32e03af7c4c4c0fa767b7",
    "status": "active",
    "next_sequence": 6,
    "last_acked_sequence": 5,
    "run_id": "791c"
  },
  "accepted": 3,
  "duplicates": 0,
  "projected": 3,
  "refused": [],
  "acknowledged_through": 5,
  "next_sequence": 6,
  "run_id": "791c"
}
```

The complete Pydantic request and response classes are defined in `developer_session_models.py`. Unknown fields are rejected on write models, file changes require canonical repository-relative POSIX paths, activity payload size is bounded, and event batches are limited to 100 events and 2 MiB of encoded event data. Activity responses expose projection attempts plus last- and next-attempt timestamps for operational diagnosis.[5]

## 6. Ordering and replay rules

**The server is the ordering authority.** A new event must match `developer_sessions.next_sequence`. The server increments that value in the same SQLite transaction that inserts the event.

**The client reserves before sending.** The adapter stores its next sequence and outbound queue under `~/.meshagent`. A process lock serializes hooks for one session.

**Exact replay is safe.** The server accepts only the same `event_id`, `source_event_id`, sequence, occurrence time, event type, and payload hash as a duplicate. It rejects reuse of either identity with different immutable content.

**Repeated observations remain distinct.** A new editor occurrence gets a random `evt_...` identifier even when its payload is textually identical to an earlier save. Retries reuse the queued event identifier.

**Acknowledgement follows commit.** The adapter removes a queue record only after it receives a valid server response. It uses a recoverable `.inflight` file while sending. A process crash leaves the claimed records available for the next hook invocation.[3]

**Bounded storage never creates a server sequence gap.** Queue limits are admission control, not permission to truncate accepted records. The adapter preserves the complete oldest undelivered prefix, including the immutable session opener. When the count or byte limit is full, a new observation is not retained; the hook remains non-blocking, rolls back the unused sequence reservation, and records an operator-visible backpressure counter. The next successful callback resumes at the next contiguous sequence.

**Terminal sessions reject new events but accept exact retries.** This closes a common ambiguity when the session-end response is lost after the server commits it.

## 7. Policy evaluation model

The package gate remains synchronous because a pre-tool hook needs a decision before the install executes. The adapter sends package and version to `/api/gate/package`. It blocks only the explicit `block` verdict. It then writes the complete result to the session as `policy.evaluated`, including the policy text, reasons, advisory records, worst severity, and any feed-unavailable explanation.[3] [4]

This gives the product two distinct facts:

1. The gate answered a policy question before execution.
2. The package later appeared in a completed shell command.

Keeping both events avoids claiming that an allowed package was installed, or that an installed package was necessarily checked.

## 8. Security and data handling

A paired adapter uses a recording-only device token. That credential may open sessions, append activity, and call the package gate. It cannot read other sessions, inspect the fleet, make CISO decisions, or delete governed memory. In local development only, `MESHAGENT_USER` may assert an unverified identity; the server records `verified=false` rather than presenting it as proven identity.[1] [3]

A repository must opt in through `.meshagent.json`. File patterns are enforced before content enters the local queue. The API independently rejects absolute, traversal, drive-qualified, backslash, and non-canonical file paths before persistence. Absolute workstation paths are not sent as repository identifiers. Operators should still treat transmitted source, tool commands, task prompts, and package names as sensitive customer data and set retention accordingly.

SQLite uses write serialization, WAL journaling, foreign keys, a 30-second busy timeout, and `synchronous=FULL`. Production deployments must keep `developer-sessions.sqlite3` in the same protected and backed-up `MESHAGENT_DB_DIR` as the run registry, control plane, audit log, and HyperMesh stores.[1] [6]

## 9. Implemented validation

The automated tests cover session replay identity, owner isolation, accurate pagination totals, sequence gaps, terminal replay, terminal late-write rejection, aggregate batch limits, repository-relative file paths, policy history, collision-safe run correlation, automatic transient projection recovery, migration compatibility, identical repeated file saves, contiguous queue backpressure recovery, immutable opener reuse, and recovery of a process-crash `.inflight` queue.[7]

A final live Cursor adapter validation opened real engine-backed session `ses_8424dfb14d95dcd23acda2628607c422` for the task **“Validate the production Developer session backend.”** Run `1030` completed with six ordered events: session start, package-policy evaluation, shell completion, package installation, file change, and session end. Every event reached `projected` state. The `httpx@0.27.2` policy evaluation was linked to its source activity, and the resulting HyperMesh run contained the edited `app.py` module.

## 10. Developer frontend integration

The production web application now uses this API as the Developer landing experience. **Sessions** lists the current developer's connected Cursor and Claude Code work, status, repository, last activity, sequence progress, and HyperMesh run linkage. **Activity** renders the server-accepted event order and shows each record's projection state, retry information, timestamps, payload digest, and governed run. **Security** presents package-policy evaluations separately from package request or installation observations and highlights unavailable advisory coverage or unresolved projection failures.

The legacy task runner remains available as an explicitly secondary demo route. Developer navigation now starts with Sessions, Activity, and Security; Analyst and CISO routes remain capability guarded and unchanged. Active screens use bounded polling against the owner-scoped read APIs. A future increment may replace polling with a server stream and add server-side cursors for inventories beyond the current bounded pages.

For large deployments, move this SQLite ledger behind the same single-writer service boundary or migrate it to PostgreSQL before horizontal API scaling. Multi-tenant partitioning, event-bus fan-out, cross-region replication, and organization-wide retention policy are not claimed by this single-tenant v1 implementation.

## References

[1]: ../services/api/app/developer_sessions.py "Developer session durable ledger implementation"
[2]: ../services/api/app/session_projection.py "Ordered activity-to-HyperMesh projection"
[3]: ../cli/meshagent_cli/protocol.py "Shared Cursor and Claude Code session protocol"
[4]: ../adapters/cursor/meshagent_hook.py "Cursor adapter implementation"
[5]: ../services/api/app/developer_session_models.py "Developer session API wire models"
[6]: ../services/api/app/migrations/001_developer_sessions.sql "Developer session SQLite migration"
[7]: ../services/api/tests/test_developer_sessions.py "Developer session integration tests"
[8]: ../services/api/app/migrations/002_projection_retry.sql "Projection retry and correlation upgrade migration"
