# Stage 1B — Corporate recorder enrollment and device trust

## Status

**Integrated milestone; production key-store gate open.**

This release adds the server control plane, native credential lifecycle, and browser workflows needed to demonstrate enterprise recorder enrollment end to end. It does not call a development software key hardware-backed. The Go recorder refuses the development key adapter when `MESHAGENT_ENV=production`.

Stage 1B is complete only after native macOS, Windows, and Linux OS-backed key adapters and their real-OS acceptance matrix pass. Code signing, notarization, packages, release manifests, SBOM provenance, and managed distribution are Stage 1C.

## Shipped backend

- Separate durable recorder trust database and migration.
- Corporate device authorization flow:
  - `POST /api/v2/recorders/enrollments`
  - `GET /api/v2/recorders/enrollments/{userCode}`
  - `POST /api/v2/recorders/enrollments/approve`
  - `POST /api/v2/recorders/token`
- Signed effective configuration and content-free health:
  - `GET /api/v2/recorders/config`
  - `POST /api/v2/recorders/heartbeat`
- Platform Administrator APIs:
  - `GET /api/v2/recorders/onboarding`
  - `GET /api/v2/recorders`
  - `GET /api/v2/recorders/{deviceId}`
  - `POST /api/v2/recorders/{deviceId}/quarantine`
  - `POST /api/v2/recorders/{deviceId}/revoke`
- Ten-minute ES256 access tokens bound to the enrolled P-256 public key.
- DPoP verification binds method, canonical target, proof time, proof ID, access-token hash, and public-key thumbprint.
- Durable proof replay prevention.
- Server trust lookup on every content-bearing request, configuration request, and heartbeat.
- Versioned trust actions, idempotency keys, conflict responses, immutable receipts, and immediate enforcement.
- Existing bearer recorders imported as visibly labelled legacy records; no silent credential upgrade.
- Bounded pending-enrollment storage with expired-request pruning.
- Client-asserted platform attestation is rejected until a real verifier is configured.
- Production startup requires a private recorder signing key and a non-overlapping Platform Administrator OIDC group.

## Shipped native recorder behavior

- Browser-based corporate enrollment with a complete no-copy verification URL.
- Automatic best-effort opening of the operating-system browser; printed URL remains the fallback.
- ES256 DPoP proof creation for token issue, refresh, configuration, heartbeat, session ingestion, event ingestion, and package gate calls.
- Short-lived access tokens remain memory-only.
- Pinned configuration-signing public key and key ID are persisted; refresh cannot replace them.
- Signed configuration is verified locally for signature, device binding, digest, expiry, and monotonic version.
- A previously verified, unexpired signed configuration may support bounded offline startup.
- Verified and applied configuration versions are tracked separately. Background refresh never claims new settings are active; the recorder reports a version only after every effective startup value is validated, applied, and the encrypted queue opens successfully. A later policy version remains visibly stale until a safe restart applies it.
- Stage 1A raw queue keys migrate into an authenticated wrapped-key envelope.
- Content-free heartbeat reports only recorder/platform/configuration/adapter/queue/delivery state.
- Trust rejection retains the encrypted causal queue and prevents hot retry.

## Shipped browser workflows

### Corporate employee

`/connect/recorder?code=ABCD-2345`

- Requires the server-authenticated corporate session.
- Only the Developer role may review and approve its recorder enrollment.
- Shows device label, deployment, operating system, recorder version, public-key thumbprint, and constrained recording grants.
- Never asks the user to paste a credential.

### Platform Administrator

`/admin/onboarding/recorder`

- Shows identity-provider, signing-authority, enrollment, active, attention, and legacy readiness.
- Does not expose Developer prompts, repositories, source paths, commands, package names, sessions, Analyst cases, or CISO decisions.

`/admin/recorders` and `/admin/recorders/{deviceId}`

- Content-free recorder identity and health.
- Durable trust URL.
- Capability-derived quarantine and revoke controls.
- Expected-version and idempotency enforcement.
- Immutable receipt/history trail.
- Honest re-enrollment path: a new device identity, never reactivation of a revoked key.

## Role boundary

The production identity model remains non-composable:

| Role | Recorder enrollment | Recorder fleet | Trust mutation | Developer/governance data |
|---|---|---|---|---|
| Developer | Review and approve own one-time device code | No | No | Existing owner-scoped Developer access |
| Analyst | No | No | No | Existing Analyst access only |
| CISO | No | No | No | Existing CISO access only |
| Platform Administrator | No owner impersonation | Content-free read | Quarantine and revoke | No automatic Developer, Analyst, or CISO access |

## Security invariants

1. A stolen access token without the device key cannot be used.
2. A DPoP proof cannot be replayed.
3. A proof for another method or target is rejected.
4. A key other than the enrolled key cannot claim the device code.
5. Quarantine or revocation denies the next request, even if its token has not expired.
6. Short-lived tokens are never written to native state.
7. The control-plane signing key is independent of every recorder device key.
8. The Platform Administrator view is intentionally content-free.
9. Browser roles never override server-issued capabilities in production.
10. Development software keys cannot be enabled in production with an environment bypass.
11. Hardware/OS attestation labels are not accepted without cryptographic verification.
12. Stage 1B does not claim Stage 1C package signatures or notarization.
13. A fetched signed policy is never reported as applied before its effective runtime settings are active.

## Production configuration

Required additions:

```text
MESHAGENT_PLATFORM_ADMIN_GROUPS=<non-overlapping OIDC group>
MESHAGENT_DEPLOYMENT_ID=<immutable customer deployment identifier>
MESHAGENT_RECORDER_SIGNING_KEY_FILE=/run/secrets/meshagent_recorder_signing_key
```

The signing key must be a P-256 private key in a private regular file. Symlinks and group/world-readable files are rejected in production. The hardened Compose profile mounts it read-only to the non-root API account.

## Demonstration flow

1. Start the durable control plane and web application.
2. Start the recorder in development enterprise mode and run:
   `meshagent-recorder enroll --label "Maya laptop" --deployment "acme-prod"`.
3. The recorder opens `/connect/recorder?code=…`.
4. Maya signs in and reviews the bounded device/grant summary.
5. Maya approves; the recorder proves possession, receives a ten-minute DPoP credential, verifies signed configuration, and starts content-free heartbeat.
6. Avery, the Platform Administrator, opens `/admin/onboarding/recorder`, then the durable recorder detail.
7. A normal Cursor event flows through the encrypted queue into the existing Developer → Analyst → CISO workflow.
8. Avery quarantines the recorder with a reason. The next recorder request is denied and its encrypted causal queue is retained.
9. The receipt appears in the administrator history. Re-enrollment creates a new device identity.

## Remaining Stage 1B gate

The following are deliberately **not** claimed as complete:

- macOS Keychain/Secure Enclave device signing and queue-key wrapping;
- Windows CNG/TPM non-exportable signing and protected queue-key wrapping;
- Linux Secret Service/TPM2 signing and wrapping;
- nonce-bound platform attestation verification;
- real-OS key-loss, migration, sleep/wake, logout/login, and secure-delete acceptance.

Until these pass, the milestone is appropriate for an integrated enterprise workflow demonstration and backend/frontend review, not a production endpoint rollout.
