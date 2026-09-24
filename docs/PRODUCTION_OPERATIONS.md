# MeshAgent Production Operations Runbook

**Status:** Authoritative for MeshAgent production v1 operations. **Owner:** platform operations. **Review cadence:** at least once per release and after every material incident. This runbook describes the repository as implemented; it does not certify regulatory compliance, fault tolerance, or security outcomes that have not been independently assessed.

## 1. Operating model and required production invariants

MeshAgent production v1 is a **single-tenant, customer-network deployment** consisting of a browser-facing web application, a FastAPI control plane, a selected gateway, and durable state. In the supported production mode, `EngineGateway` writes governed-memory records through the bundled HyperMesh Python layer and C core. The API also persists control-plane records under `MESHAGENT_DB_DIR`: HyperMesh stores, `index.json`, `control-plane.sqlite3` for cases and governance workflows, `developer-sessions.sqlite3` for connected-agent sessions and ordered activity, `browser_sessions.sqlite3` for hashed opaque sessions and one-time PKCE transactions, `devices.json`, the operation journal, and the hash-chained `audit.jsonl` log. These records must stay together in one durable volume.

> **Production invariant:** `MESHAGENT_ENGINE=1`, a non-temporary `MESHAGENT_DB_DIR`, OIDC configuration, and a TLS-terminating ingress are mandatory for a production claim. The API health endpoint exposes whether engine mode, durable state, and an identity provider are enabled; use it as evidence of configuration, not as a substitute for a security assessment.

Before operating a deployment, assign a service owner, on-call contact, identity-provider owner, backup owner, and security incident commander. Provision storage that survives container replacement and is accessible only to the service account and approved backup process. Do not operate production from the implicit temporary directory used when `MESHAGENT_DB_DIR` is unset.

Before strict backup verification is adopted, generate and retain at least one legitimate audited action (for example, an approved device pairing or a test run) so that `audit.jsonl` exists. The operations scripts use `--require-audit` for production recovery validation and intentionally fail when the audit log is absent rather than treating missing evidence as a clean record.

### Production modes

| Mode                         | Intended use                               | Gateway and state                                                       | Authentication                                                       | Production status            |
| ---------------------------- | ------------------------------------------ | ----------------------------------------------------------------------- | -------------------------------------------------------------------- | ---------------------------- |
| **Local/sample**             | UI exploration and isolated development    | `SampleGateway`; state is not a production record                       | Asserted development headers may be accepted                         | **Not production**           |
| **Engine development**       | Integration testing and operator rehearsal | `EngineGateway`; state may be durable for the test                      | OIDC may be absent only when access is otherwise isolated            | Not a production environment |
| **Production single tenant** | Customer-operated workload                 | `EngineGateway`, persistent `MESHAGENT_DB_DIR`, immutable backup copies | OIDC issuer, audience, distinct Analyst and CISO groups, TLS ingress | Supported v1 mode            |

## 2. Secure deployment configuration matrix

The deployment owner must set secrets through the chosen platform secret facility; do not put secrets, private keys, access tokens, or identity files in this repository, container image, shell history, or backup metadata.

| Area               | Required production configuration                                                                                                                                                                                                                                                  | Validation and operational note                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Engine and state   | `MESHAGENT_ENGINE=1`; absolute `MESHAGENT_DB_DIR` on durable storage; one API writer process per state directory unless concurrency behavior has been validated                                                                                                                    | `GET /api/health` reports `gateway: EngineGateway` and `durable: true`. Snapshot the complete state directory, not selected files.                                                                                                                                                                                                                                                                                                                                                               |
| Human identity     | `MESHAGENT_OIDC_ISSUER`, `MESHAGENT_OIDC_AUDIENCE`, public `MESHAGENT_OIDC_CLIENT_ID`, `MESHAGENT_OIDC_ROLE_CLAIM`, non-empty `MESHAGENT_ANALYST_GROUPS`, and non-empty `MESHAGENT_CISO_GROUPS`; optionally `MESHAGENT_OIDC_SCOPE` and `MESHAGENT_OIDC_JWKS_URL`                   | The API owns discovery, Authorization Code + PKCE, token exchange and verification, and opaque HttpOnly sessions. It validates issuer, expiry, audience, exact browser origins, and non-overlapping Analyst/CISO groups. The static bundle receives only public issuer/client indicators used to choose the sign-in boundary; access tokens never enter browser storage. Confirm `identity_provider: true`; test expiry, logout, Developer isolation, Analyst casework, and CISO-only mutations. |
| API exposure       | Put the API behind a customer-managed TLS ingress; restrict network access to approved browser, automation, and IdP paths; set exact `MESHAGENT_CORS_ORIGINS` and `MESHAGENT_WEB_URL`                                                                                              | Do not expose development ports or allow arbitrary origins. The repository compose file is a local starting point, not a complete production perimeter.                                                                                                                                                                                                                                                                                                                                          |
| Device recorders   | Approve device pairing only through authenticated humans; periodically list and revoke inactive devices                                                                                                                                                                            | Device tokens are intentionally recording-only. Treat their storage on endpoints as a credential-management concern.                                                                                                                                                                                                                                                                                                                                                                             |
| Audit and evidence | Preserve `audit.jsonl`, `index.json`, and state stores in every backup; configure alerting on audit-chain failure                                                                                                                                                                  | The audit chain detects line changes or removals unless an attacker rewrites the chain. It is not externally signed in v1. Export or independently protect evidence if stronger tamper resistance is required.                                                                                                                                                                                                                                                                                   |
| Backup encryption  | Set `MESHAGENT_ENCRYPT_HOOK` to `scripts/ops/encrypt-age.sh` or an approved equivalent, and keep the private recovery identity separately governed                                                                                                                                 | The hook receives only input and output paths. Encryption is an integration point; key custody, rotation, and recovery authorization remain deployment responsibilities.                                                                                                                                                                                                                                                                                                                         |
| Supply-chain feeds | Decide whether `MESHAGENT_FEEDS=1` is permitted, establish egress rules and timeouts, and understand the degradation behavior                                                                                                                                                      | When disabled or unavailable, the product reports unavailability rather than inventing a version. Feed results do not replace vulnerability-management decisions.                                                                                                                                                                                                                                                                                                                                |
| Model integration  | Keep `OPENAI_API_KEY` in a secret manager; set `OPENAI_BASE_URL` for an approved compatible endpoint and set `MESHAGENT_MODEL` to a model that endpoint actually exposes. The current default is `gpt-5-mini`. Approve outbound destinations and data handling before enabling it. | No model key means new runs record the clearly labelled reference build. Invalid or unsupported model responses fail the run with a durable, redacted explanation rather than leaving the UI at “stated the task.”                                                                                                                                                                                                                                                                               |
| Operating account  | Run the service as a non-root account with least filesystem privilege; ensure backups are readable only to the backup and recovery roles                                                                                                                                           | Test ownership and access after deployment and restore. Containers do not alone provide tenant or host isolation.                                                                                                                                                                                                                                                                                                                                                                                |

### Baseline production acceptance

1. Record the deployment release identifier, image digests, change ticket, state-volume identifier, and configuration-owner approvals.
2. Confirm `curl -fsS https://<api-host>/api/health` reports status `ok`, engine mode, durable state, and an enabled identity provider. Keep the response in the change record without copying sensitive configuration.
3. Sign in as a Developer, Analyst, and CISO. Confirm the browser contains only an opaque HttpOnly session cookie, not an access token. Confirm that a Developer cannot access another developer's non-seeded run; an Analyst can investigate fleet evidence and manage cases but cannot create policy or approve recommendations; and a CISO can use governance mutations. Confirm that a requester cannot approve their own exception and that expired/revoked sessions terminate live streams.
4. Pair a dedicated test device, record a benign event, then revoke it and confirm a subsequent recorder request is rejected.
5. Create an encrypted backup, verify it, and complete the disaster-recovery drill procedure before accepting a new environment.

## 3. Backup, checksum, encryption, restore, and verification

The scripts in [`scripts/ops/`](../scripts/ops/) operate only on an explicitly supplied state directory. They are intentionally deployment-neutral: the platform owner supplies quiesce, resume, encryption, decryption, and health hooks because this repository does not own the service manager, KMS, or ingress. Hooks must be absolute executable paths, not shell snippets.

### Application-consistent backup

A file copy while HyperMesh or the API is writing is not asserted to be application-consistent. The default backup flow requires a quiesce hook before archiving and a resume hook afterward. The hook must drain or stop all API writers and return only after they are inactive. Use the offline escape hatch only during a declared maintenance window after all writers are stopped; it requires the exact acknowledgment string to make the operator decision auditable in command history.

```bash
cd /home/ubuntu/meshagent-production-v1
export MESHAGENT_DB_DIR=/srv/meshagent/state
export MESHAGENT_BACKUP_ROOT=/srv/meshagent/backups
export MESHAGENT_QUIESCE_HOOK=/opt/meshagent/hooks/quiesce
export MESHAGENT_RESUME_HOOK=/opt/meshagent/hooks/resume
export MESHAGENT_RELEASE=0.1.0
# Recommended: a recipient public key; retain the private identity outside this host.
export MESHAGENT_ENCRYPT_HOOK="$PWD/scripts/ops/encrypt-age.sh"
export MESHAGENT_BACKUP_AGE_RECIPIENT=age1replace-with-your-approved-recipient
scripts/ops/backup.sh --label nightly
```

The resulting backup directory contains `metadata.env`, an encrypted or plaintext payload, and `MANIFEST.sha256`. The manifest covers all files except itself. A backup is not accepted merely because the archive was created: verify it immediately and record its ID and manifest verification result.

```bash
export MESHAGENT_DECRYPT_HOOK="$PWD/scripts/ops/decrypt-age.sh"
export MESHAGENT_BACKUP_AGE_IDENTITY_FILE=/secure/recovery/meshagent-age-identity.txt
scripts/ops/verify-backup.sh --backup /srv/meshagent/backups/<backup-id> --require-audit
```

For a planned fully offline backup, stop all MeshAgent writers using the platform procedure and then run:

```bash
export MESHAGENT_BACKUP_OFFLINE_ACK=I_HAVE_STOPPED_ALL_MESHAGENT_WRITERS
scripts/ops/backup.sh --offline --label maintenance
```

### Restore into a clean directory

Never restore over a running or non-empty state directory. The restore script rejects a non-empty target, verifies the manifest before extraction, rejects tar paths with absolute or parent traversal, restores without preserving archive ownership, and then validates `index.json` and the audit hash chain.

```bash
scripts/ops/restore.sh \
  --backup /srv/meshagent/backups/<backup-id> \
  --target /srv/meshagent/recovery/state \
  --require-audit
```

Use the recovered directory first in an isolated environment. Configure the isolated API with `MESHAGENT_DB_DIR=/srv/meshagent/recovery/state`, then validate `/api/health`, authorized API access, representative run listing, and the audit endpoint as a security-office user. Do not point production traffic at recovered state until an incident commander or change approver records acceptance.

### Retention and legal hold

The default policy target is **35 days of backups and at least seven newest backup sets**, but the retention owner must change those values to match the approved business, contractual, and legal schedule. Retention does not make a compliance claim. A legal hold file lists backup IDs that must not be deleted; that file must be access controlled and managed independently of the backup script.

```bash
# Review first. No deletion occurs without --apply.
scripts/ops/retention.sh --backup-root /srv/meshagent/backups \
  --keep-days 35 --keep-count 7 --hold-file /etc/meshagent/legal-holds.txt
# Apply only after an approver reviews the plan.
scripts/ops/retention.sh --backup-root /srv/meshagent/backups \
  --keep-days 35 --keep-count 7 --hold-file /etc/meshagent/legal-holds.txt --apply
```

Replication targets, object-storage lifecycle rules, and immutable-storage settings can retain data beyond the local retention plan. The records manager must include those copies in any deletion or hold decision.

## 4. Disaster-recovery drill

Run a full drill at least quarterly and after a backup, storage, encryption, or deployment-process change. The drill is successful only when a selected backup is checksum-verified, decrypted and restored to isolated clean storage, state verification passes, and an isolated application instance passes health and representative authorization checks. The recovery time and recovery point achieved must be measured and compared with business objectives; this repository does not prescribe them.

1. Select a recent backup and record its ID, creation time, encrypted status, release, and expected recovery point.
2. Prepare an isolated network and empty recovery workspace. Do not share the production state volume or public ingress.
3. Supply a `MESHAGENT_DR_HEALTH_HOOK` that starts or interrogates the isolated deployment and returns nonzero unless it confirms, at minimum: `/api/health` is `ok`; engine and durable state are enabled; OIDC is enabled; an analyst can query audit evidence; and representative run inventory appears. The hook receives `drill <restored-state-dir>`.
4. Run the drill and retain its generated report in the change or DR record.

```bash
export MESHAGENT_DR_HEALTH_HOOK=/opt/meshagent/hooks/dr-health
scripts/ops/drill.sh --backup /srv/meshagent/backups/<backup-id> \
  --work-root /srv/meshagent/drill-work --keep-restored
```

`--metadata-only` verifies artifacts and portable control-plane state but does **not** prove application recovery. Record it as a backup integrity check, not a DR drill. After a failed drill, preserve evidence, open a corrective action, and do not mark the backup path healthy until the drill is rerun successfully.

## 5. Upgrade preflight and rollback

### Upgrade preflight

Before a production release, complete the [release checklist](../CHANGELOG.md#release-checklist), make a verified backup, and deploy the candidate in an isolated or canary environment using a copy of state. The preflight script rejects a candidate that does not report engine mode, durable state, and OIDC identity. It does not migrate data or deploy artifacts.

```bash
scripts/ops/upgrade-preflight.sh \
  --candidate-api-url https://candidate-api.example.internal \
  --state-dir /srv/meshagent/state \
  --backup /srv/meshagent/backups/<backup-id> \
  --expected-version 0.1.0
```

During the change, monitor API availability, authorization failures, recorder failure rate, audit-chain verification, state-volume errors, and latency. Stop rollout for an unexpected security boundary failure, irreversible state error, sustained service-level breach, or audit-chain failure. Preserve diagnostics without collecting token values, source bodies, prompts, or private keys.

### Rollback

Rollback is a controlled recovery, not an in-place overwrite. Identify the last known-good release and backup, stop or drain all writers, restore state into a new empty directory, and hand the atomic service and state cutover to a deployment-owned hook. Preserve the failed release and its state copy until incident command closes investigation.

```bash
export MESHAGENT_ROLLBACK_ACTIVATE_HOOK=/opt/meshagent/hooks/rollback-activate
export MESHAGENT_ROLLBACK_HEALTH_HOOK=/opt/meshagent/hooks/rollback-health
scripts/ops/rollback.sh \
  --backup /srv/meshagent/backups/<backup-id> \
  --target /srv/meshagent/recovery/rollback-state \
  --release 0.1.0
```

The activation hook receives `rollback <restored-state-dir> <release>` and must implement platform-specific traffic drain, service stop, release activation, and atomic state cutover. The health hook receives the same arguments. If no health hook is configured, perform and record the same validation manually before reopening traffic.

## 6. Observability and service-level plan

Production v1 does not include a complete metrics, tracing, alerting, or log-redaction implementation. The following plan defines operational requirements to be implemented by the deployment and reviewed after each release. Avoid exporting governed-memory content, tokens, authorization headers, prompts, raw source bodies, device tokens, or encryption identities to telemetry.

| Signal                      | Collection and alert intent                                                                                                                                                   | Initial operating objective                                                                                                           |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| API health and availability | Poll authenticated edge paths separately from public `/api/health`; alert on consecutive failures and failed readiness after deployment                                       | **Target:** 99.9% monthly successful health probes, excluding approved maintenance. This is a target, not an established SLO.         |
| API latency and errors      | Capture request method, route template, status class, duration, correlation ID, and gateway mode; alert on elevated 5xx or latency regression                                 | **Target:** 99% of read API requests under 1 second at the ingress, with workload-specific baselines established before enforcement.  |
| Authorization boundary      | Count 401/403/404 access-denied outcomes and analyst-only access attempts by route and principal type, without recording credentials                                          | Investigate unexpected spikes, especially after identity or client changes.                                                           |
| Recorder health             | Count accepted, refused, and unexplained recorder events; measure ingestion duration and device revocations                                                                   | Alert when recording failures exceed the deployment baseline or when unapproved devices appear.                                       |
| State and audit integrity   | Run `verify-state.sh --require-audit` after backup and on a scheduled controlled job; capture disk space, filesystem errors, backup age, checksum verification, and drill age | Page on audit-chain break, storage I/O error, failed verification, or no successful backup within the approved recovery-point window. |
| Security inputs             | Track advisory-feed availability, scan ingestion outcomes, and model/third-party dependency errors without event payloads                                                     | Treat unavailable feeds as reduced visibility, not proof of no findings.                                                              |
| Change safety               | Record release ID/image digest, migration or rollback action, backup ID, health result, and operator                                                                          | Require an accountable change record for deployment, rollback, deletion, hold, and recovery actions.                                  |

Every alert needs an owner, severity, runbook link, and test interval. Review alert noise monthly. Correlate user-visible failures with a request ID generated at ingress or provided by the deployment; v1 application responses do not guarantee a request-ID header.

## 7. Incident response

**First response:** acknowledge, assign an incident commander, preserve evidence, establish a secure communications channel, and record the time and scope. Do not use incident chat to paste raw governed memory, secrets, recovery identities, or access tokens.

| Scenario                                          | Immediate containment                                                                                                                           | Investigation and recovery                                                                                                                                                                                 |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Suspected credential or device-token exposure     | Revoke the affected device through the API; disable or rotate the relevant identity-provider credential; restrict ingress if scope is uncertain | Preserve relevant audit records and identity-provider events. Determine whether recorder writes need review; rotate credentials and validate pairing flows before re-enabling.                             |
| Unauthorized access or privilege-boundary failure | Restrict ingress or disable affected principal/group; preserve application, ingress, and identity-provider logs                                 | Confirm developer/analyst isolation with representative tests. Restore or roll back only with an approved, evidence-preserving plan.                                                                       |
| Audit-chain failure                               | Treat audit evidence as suspect; immediately copy the affected state read-only and stop destructive changes                                     | Run `verify-state.sh`, compare independent backups, determine first broken line, and escalate to security and records owners. The chain is not externally signed, so scope must be independently assessed. |
| State corruption or storage failure               | Quiesce writers; capture filesystem and service evidence; do not repeatedly restart into the same state                                         | Restore the latest verified backup to a clean directory, validate in isolation, then follow rollback or recovery change control.                                                                           |
| Backup or decryption failure                      | Keep current state intact; do not delete the failed backup artifact                                                                             | Verify manifests, hook versions, recipient/identity custody, and storage access. Perform a new backup and a full drill after remediation.                                                                  |
| Suspected data disclosure or deletion error       | Stop relevant exports and destructive jobs; place affected backup IDs under hold where appropriate                                              | Engage legal/privacy and security owners. Preserve certificates, audit log, configuration records, and backup inventory; follow the approved notification process.                                         |

Close an incident only after containment, evidence preservation, root-cause analysis proportionate to impact, recovery validation, customer/records decisions where needed, and tracked corrective actions. Rehearse the most likely failure modes, including loss of the backup key path and a failed identity-provider dependency.

## 8. Deletion, retention, and legal hold

The product's `forget` operation deletes governed-memory content through the engine and returns a deletion certificate. It is not a global erasure workflow. Certificates and audit evidence can remain, and immutable or replicated backups may preserve data until retention expiry or an approved purge process. The deletion request owner must understand and communicate this scope before executing it.

1. Authenticate the requester and verify authorization, request scope, reason, and applicable contract, privacy, litigation, and records obligations.
2. Check the legal-hold register before calling `forget`, changing retention, or purging backup copies. If any hold applies, pause destructive action and obtain written release from the authorized legal or records owner.
3. Use `GET /api/runs/{run_id}/forget/preview` to document the proposed closure. For a human-initiated deletion, supply the observed version through `If-Match` and a unique idempotency key when calling `POST /api/runs/{run_id}/forget`.
4. Preserve the returned deletion certificate and related audit record in the case file. Verify the run's resulting state according to the product workflow; do not infer deletion of every external copy.
5. Identify backup, replication, export, and disaster-recovery copies containing the material. Place related backup IDs in the hold file if a hold applies; otherwise process expiry or approved targeted purge under the backup and records policies.
6. Record limitations, including any copy that cannot be removed immediately because of technical, legal, or contractual controls.

The retention script only deletes backup directories selected by its policy after a manifest check. It does not discover all external copies, revoke credentials, erase third-party model-provider data, or decide legal obligations.

## 9. Compatibility and support boundaries

| Component or interface             | v1 compatibility position                                                                           | Operational boundary                                                                                                             |
| ---------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| API state format                   | `metadata.env` backup format `meshagent-state-v1`; registry format version 1                        | Restore only with scripts from the same or explicitly tested release family. Test upgrades and restores before changing version. |
| REST API                           | `/api/health` is used by runbooks; other `/api/*` endpoints follow the shipped application contract | The archived `/api/v1` contract is non-authoritative and does not describe the current service.                                  |
| Gateway modes                      | `EngineGateway` is production; `SampleGateway` is development/demo                                  | Do not treat sample responses or non-durable mode as production evidence.                                                        |
| Identity                           | OIDC with asymmetric RS/ES algorithms configured in the service                                     | Provider-specific claims, conditional access, availability, and group lifecycle are deployment-owned.                            |
| Storage                            | Local or mounted filesystem visible to the API service                                              | Distributed filesystems, multi-writer operation, snapshots, and restore semantics require deployment-specific validation.        |
| Backup encryption                  | age hook supplied; an equivalent approved executable hook may be used                               | Key issuance, escrow, HSM/KMS use, rotation, and recovery authorization are outside the application.                             |
| External feeds and model endpoints | Optional OpenAI-compatible model endpoint and optional advisory feeds                               | Egress, data processing, rate limits, availability, and terms are customer responsibilities.                                     |
| Browsers and editor adapters       | Current shipped web UI plus Cursor, Claude Code, and MCP adapters                                   | Endpoint hardening, adapter distribution, desktop security, and unsupported-editor behavior are outside v1 support.              |

See [SUPPORT.md](../SUPPORT.md) for triage information and [SECURITY.md](../SECURITY.md) for vulnerability reporting. The authoritative architecture is in the root [README](../README.md); the legacy material in [`docs/archive/`](archive/) is historical only.

## 10. Operational command reference

| Outcome                             | Command or procedure                                                                                               |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Create quiesced encrypted backup    | `scripts/ops/backup.sh --label <label>` with quiesce/resume and encryption hook environment configured             |
| Verify a backup                     | `scripts/ops/verify-backup.sh --backup <dir> --require-audit`                                                      |
| Restore safely                      | `scripts/ops/restore.sh --backup <dir> --target <absolute-empty-dir> --require-audit`                              |
| Verify live/restored portable state | `scripts/ops/verify-state.sh --state-dir <dir> --require-audit`                                                    |
| Review / apply retention            | `scripts/ops/retention.sh --backup-root <dir> ...` / add `--apply` after review                                    |
| Run application recovery drill      | `scripts/ops/drill.sh --backup <dir> --work-root <dir>` with `MESHAGENT_DR_HEALTH_HOOK`                            |
| Validate upgrade candidate          | `scripts/ops/upgrade-preflight.sh --candidate-api-url <url> --state-dir <dir> --backup <dir>`                      |
| Hand off controlled rollback        | `scripts/ops/rollback.sh --backup <dir> --target <empty-dir> --release <release>` with activation and health hooks |

All scripts fail closed on missing required input and avoid overwriting state. Read each script's `--help` output and run it in a non-production rehearsal before adopting it for a production schedule.
