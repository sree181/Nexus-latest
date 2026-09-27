CREATE TABLE IF NOT EXISTS recorder_devices (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  credential_kind TEXT NOT NULL CHECK (credential_kind IN ('dpop','legacy_bearer')),
  subject TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  verified INTEGER NOT NULL CHECK (verified IN (0,1)),
  deployment TEXT NOT NULL,
  platform TEXT NOT NULL,
  platform_version TEXT NOT NULL,
  architecture TEXT NOT NULL,
  recorder_version TEXT NOT NULL,
  public_jwk_json TEXT,
  key_thumbprint TEXT,
  attestation_format TEXT,
  trust_state TEXT NOT NULL CHECK (trust_state IN ('pending','active','quarantined','revoked')),
  version INTEGER NOT NULL,
  config_version INTEGER NOT NULL DEFAULT 1,
  reported_config_version INTEGER NOT NULL DEFAULT 0,
  config_digest TEXT,
  enrolled_at INTEGER NOT NULL,
  approved_at INTEGER NOT NULL DEFAULT 0,
  last_seen_at INTEGER NOT NULL DEFAULT 0,
  queue_batches INTEGER NOT NULL DEFAULT 0,
  queue_bytes INTEGER NOT NULL DEFAULT 0,
  oldest_queued_age_seconds INTEGER NOT NULL DEFAULT 0,
  adapter_state TEXT NOT NULL DEFAULT 'unknown',
  delivery_state TEXT NOT NULL DEFAULT 'unknown',
  legacy_retire_at INTEGER,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS recorder_devices_trust_idx ON recorder_devices(trust_state, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS recorder_devices_subject_idx ON recorder_devices(subject);

CREATE TABLE IF NOT EXISTS recorder_enrollments (
  id TEXT PRIMARY KEY,
  user_code TEXT NOT NULL UNIQUE,
  device_code_hash TEXT NOT NULL UNIQUE,
  request_digest TEXT NOT NULL,
  request_json TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  approved_at INTEGER NOT NULL DEFAULT 0,
  approved_subject TEXT NOT NULL DEFAULT '',
  approved_name TEXT NOT NULL DEFAULT '',
  approved_email TEXT NOT NULL DEFAULT '',
  approved_verified INTEGER NOT NULL DEFAULT 0,
  claimed_at INTEGER NOT NULL DEFAULT 0,
  device_id TEXT
);

CREATE TABLE IF NOT EXISTS recorder_dpop_replay (
  jti TEXT PRIMARY KEY,
  key_thumbprint TEXT NOT NULL,
  method TEXT NOT NULL,
  target TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS recorder_dpop_replay_expiry_idx ON recorder_dpop_replay(expires_at);

CREATE TABLE IF NOT EXISTS recorder_configurations (
  device_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  digest_sha256 TEXT NOT NULL,
  signature TEXT NOT NULL,
  kid TEXT NOT NULL,
  issued_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY(device_id, version),
  FOREIGN KEY(device_id) REFERENCES recorder_devices(id)
);

CREATE TABLE IF NOT EXISTS recorder_receipts (
  receipt_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  actor_name TEXT NOT NULL,
  prior_state TEXT NOT NULL,
  next_state TEXT NOT NULL,
  resulting_version INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  correlation_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  response_json TEXT NOT NULL,
  UNIQUE(actor, action, idempotency_key),
  FOREIGN KEY(device_id) REFERENCES recorder_devices(id)
);
CREATE INDEX IF NOT EXISTS recorder_receipts_device_idx ON recorder_receipts(device_id, created_at DESC);
