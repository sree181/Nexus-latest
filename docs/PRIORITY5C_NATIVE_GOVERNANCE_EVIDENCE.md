# Priority 5C: Native HyperMesh Governance Evidence

**Status:** Implemented and validated on 24 September 2026
**Baseline:** Priority 5B commit `20f43b8`
**Author:** Manus AI

## 1. Outcome

Priority 5C makes policy and exception history a native HyperMesh evidence chain. A policy or exception mutation and its projection work now commit in the same SQLite transaction. A resident worker writes the frozen event into a dedicated HyperMesh governance store, records the returned native ULID, and acknowledges the outbox row only after both relational and native integrity checks pass.

This design preserves a deliberate authority boundary. The relational control plane remains authoritative for mutable workflow state and optimistic concurrency. HyperMesh is authoritative for immutable evidence relationships and ancestry. A failure to project never rolls back an already committed business decision, but it does make the native evidence endpoint unavailable until retry succeeds.

## 2. Native relationship vocabulary

The vocabulary is closed and persisted as a compatibility contract.

| Relationship | Meaning |
| --- | --- |
| `policy_version` | A policy version was created, submitted, withdrawn, retired, or otherwise changed without becoming effective. |
| `policy_activation` | An exact policy version became effective through an identified actor and effective interval. |
| `policy_supersession` | Activation ended the prior effective version and replaced it with the exact new version. |
| `exception_request` | An exception or renewal request bound scope, owner, controls, expiry, evidence, and one immutable policy digest. |
| `exception_decision` | An approval, rejection, or renewal supersession was recorded against the frozen request digest. |
| `exception_expiry` | An approval window or approved exception reached its durable expiry boundary. |
| `exception_revocation` | An approved exception or pending renewal was ended by an authorized actor. |

Every native episode includes the source event ID, resource ID, actor, transition, rationale, occurrence time, correlation ID, exact content or request digest, and a canonical payload SHA-256 value. Policy episodes name the exact `policy-version:<policy-id>@<version>` node. Exception episodes name the exact policy version and digest that governed the request.

## 3. Transactional projection contract

`governance_projection_outbox` is created additively during control-plane initialization. Its primary key combines the immutable event ID and relationship kind. This allows one activation event to produce both activation and supersession relations without creating duplicate work.

A lifecycle transaction follows five steps:

1. Validate identity, capability, optimistic version, lifecycle state, and separation of duties.
2. Persist the aggregate change and append the immutable policy or exception event.
3. Build a canonical event snapshot from the exact policy version or exception request present in the same transaction.
4. Insert one or more pending projection rows with the canonical JSON and SHA-256 digest.
5. Commit the mutation, event, and projection work together.

Startup deterministically backfills missing projection rows for historical events. `INSERT OR IGNORE` and the event-plus-kind key make repeated initialization idempotent.

## 4. Integrity and retry behavior

Before a native write, the worker verifies the outbox payload digest. It then compares the frozen policy content digest or exception request and policy digests with the relational source. `EngineGateway` independently recomputes the canonical digest before calling the native writer.

The HyperMesh writer uses the event ID and relationship kind as its native idempotency key. A retried event returns the existing live ULID instead of writing another episode. The control plane marks the projection complete only after the native ULID returns and the source digests still match. Interrupted `projecting` rows return to `pending` at startup. Failed rows use capped exponential retry and retain a redacted error string.

A governance evidence read succeeds only when every projection row for that resource is complete and digest-valid. Otherwise the API returns the normal not-found shape. This prevents a partial or stale graph from looking complete.

## 5. Review and case evidence linkage

Exception requests may cite `review:<id>` and `case:<id>` evidence identifiers. The control plane resolves those identifiers only through stored review records. When a review has a native `evidence_root_ulid`, the frozen exception projection includes that ULID as a resolved native boundary. Unknown identifiers remain visibly unresolved and are never promoted to native facts.

The governance store represents the boundary as review, case, and `native-root` nodes. It does not copy or synthesize the underlying Developer evidence. Authorized users follow the linked review graph through its existing scoped endpoint.

## 6. Scoped APIs and authorization

Two additive endpoints expose native governance evidence:

- `GET /api/policies/{policy_id}/evidence` requires `policy.read`.
- `GET /api/exceptions/{exception_id}/evidence` requires `exception.read`.

The response includes the resource kind and ID, projection count, acknowledged native ULIDs, canonical projection digests, and a `GraphPayload` built only from native governance episodes and their recorded ancestry. Developers lack both capabilities and receive `403` before evidence is read.

Sample mode does not fabricate governance evidence. Both endpoints remain unavailable without `EngineGateway` and a completed native projection.

## 7. Deployment and recovery

The native governance store is a sibling under `MESHAGENT_DB_DIR` and must be backed up with the control-plane database, review stores, audit chain, device registry, and other HyperMesh stores. A deployment must retain the single-writer boundary because the current HyperMesh and SQLite combination is not a multi-writer service.

After upgrade, verify that pending and failed projection counts converge, create one policy and one exception on an isolated state copy, retrieve both evidence endpoints, and restart the API. The same native ULIDs and relation counts must return after restart.

## 8. Validation evidence

The focused Priority 5C suite covers atomic enqueue, canonical payload hashes, exact policy and exception digest binding, retry state, tamper rejection, migration idempotency, native idempotency, and resource-scoped retrieval. The complete API suite passed with **519 passed and 1 skipped**.

An isolated copy of the collaborative EngineGateway state was migrated and exercised through the live API. A policy projected to one acknowledged native ULID. An approved exception projected its request and decision to two acknowledged native ULIDs. Developer access returned `403`. After restart, policy and exception ULID arrays and graph relation counts were byte-for-byte unchanged.

## 9. Boundaries

Priority 5C does not turn the relational workflow database into a graph database. It does not copy Developer source or prompts into the governance store. It does not add a general policy expression language, multi-tenant row partitioning, distributed worker claims, or external evidence signing. Those are separate product or deployment decisions.

## References

[1]: ../services/api/app/control_plane.py "Transactional governance outbox and integrity implementation"
[2]: ../services/engine/meshagent/codegraph.py "Native HyperMesh governance episode writer"
[3]: ../services/api/app/engine_gateway.py "EngineGateway projection and scoped graph retrieval"
[4]: ./PRIORITY5B_GOVERNANCE_ENFORCEMENT.md "Priority 5B governance enforcement contract"
[5]: ./PRODUCTION_OPERATIONS.md "MeshAgent production operations runbook"
