# meshAgent Cursor Engineering Guide

> **Repository:** `sree181/Nexus-latest`
>
> **Canonical branch at drafting:** `manus/figma-exact-v3`
>
> **Audited application baseline:** `9f832e7b1bf75ce6b41d67e80ebedb3aad4fa7cc`
> **Status of this guide:** practical engineering guide for working in this repository, not a production-attestation document.

## 1. Purpose, scope, and source of truth

Use this guide when opening **meshAgent Production v1** in Cursor, refining its web application, modifying API/workflow behavior, working with governed evidence, or configuring the supported Cursor recorder path.

meshAgent is a **single-tenant, customer-operated control plane for governed agent memory**. Its server-side production baseline is a React/Vite browser application behind customer-managed TLS, an API-owned FastAPI control plane, and a real HyperMesh-backed `EngineGateway`. The repository also deliberately supports sample and development modes. Native endpoint rollout remains gated by the OS-backed keystore, platform-attestation, signed-package, and managed-distribution work described in the Stage 1B/1C boundary below.

This guide covers:

- safe Git checkout, verification, and feature-branch workflow;
- the web, API, evidence, workflow, and recorder architecture;
- repository-relative locations and change boundaries;
- local development, Docker, and production configuration distinctions;
- concrete routes, endpoint families, tests, and release checks;
- safety rules for Cursor-assisted changes.

This guide **does not** claim that the repository provides:

- a GitHub App, pull-request webhook ingestion, or issue webhook ingestion;
- a managed multi-tenant service, HA topology, managed KMS, or complete observability stack;
- OS-backed native recorder keystore adapters, platform attestation, signed/notarized installers, MDM deployment, or Stage 1C rollout packaging;
- externally immutable or independently signed audit evidence.

For production operations, defer to the controlling documents named in [`README.md`](../README.md), especially [`docs/PRODUCTION_OPERATIONS.md`](PRODUCTION_OPERATIONS.md). Material under [`docs/archive/`](archive/) is historical and **non-authoritative** unless reconciled with current code and controlling documentation.

---

## 2. Establish the exact source before editing

### 2.1 Canonical remote and branch

The authoritative branch remote is:

```text
https://github.com/sree181/Nexus-latest.git
```

Use the explicit remote name **`nexus-latest`**. Do **not** assume `origin` is correct: existing clones may point `origin` to a different repository (`sree181/meshagent`), where this audited branch and PR do not exist.

The application-baseline commit in this guide is an audited reference, not a promise that the remote branch will remain frozen. Re-run the verification commands immediately before starting a change, reviewing a PR, or branching.

### 2.2 New, isolated checkout

This is the recommended first checkout. It does not depend on an existing clone or on `origin`:

```bash
REPO=https://github.com/sree181/Nexus-latest.git
BRANCH=manus/figma-exact-v3

git clone --origin nexus-latest --branch "$BRANCH" --single-branch "$REPO" meshagent-figma-exact-v3
cd meshagent-figma-exact-v3
git fetch --no-tags nexus-latest "refs/heads/$BRANCH:refs/remotes/nexus-latest/$BRANCH"
REMOTE_HEAD=$(git rev-parse "nexus-latest/$BRANCH")
test "$(git rev-parse HEAD)" = "$REMOTE_HEAD"
git status --short --branch
```

The audited application baseline before this guide was added was `9f832e7b1bf75ce6b41d67e80ebedb3aad4fa7cc`. The branch may legitimately advance with reviewed documentation or later UI refinements. The important checkout test is that local `HEAD` equals the freshly fetched remote branch head. Record that exact SHA in the task or PR before editing.

### 2.3 Existing clone, including one with an unrelated `origin`

**Stop on a dirty worktree.** Do not use `git reset --hard`, `git clean -fd`, forced checkout, or a force-push to make a clone appear current.

```bash
REPO=https://github.com/sree181/Nexus-latest.git
BRANCH=manus/figma-exact-v3

git status --porcelain
test -z "$(git status --porcelain)" || {
  echo 'Stop: commit, stash, or use a separate worktree; do not overwrite local work.' >&2
  false
}

git remote get-url nexus-latest >/dev/null 2>&1 \
  && git remote set-url nexus-latest "$REPO" \
  || git remote add nexus-latest "$REPO"
git fetch --no-tags nexus-latest "refs/heads/$BRANCH:refs/remotes/nexus-latest/$BRANCH"
git show-ref --verify --quiet "refs/remotes/nexus-latest/$BRANCH"
git switch "$BRANCH" 2>/dev/null || git switch --track -c "$BRANCH" "nexus-latest/$BRANCH"
git branch --set-upstream-to="nexus-latest/$BRANCH" "$BRANCH"
git pull --ff-only
REMOTE_HEAD=$(git rev-parse "nexus-latest/$BRANCH")
test "$(git rev-parse HEAD)" = "$REMOTE_HEAD"
git status --short --branch
```

If `git pull --ff-only` fails, stop and inspect the divergence; do not overwrite the local history.

### 2.4 Version, ancestry, and GitHub verification

```bash
BRANCH=manus/figma-exact-v3
STAGE1B=ee2ebb372b1779cdea4522c762a4fc174c7ae2cc

git fetch --no-tags nexus-latest "refs/heads/$BRANCH:refs/remotes/nexus-latest/$BRANCH"
REMOTE_HEAD=$(git rev-parse "nexus-latest/$BRANCH")
test "$(git rev-parse HEAD)" = "$REMOTE_HEAD"
git cat-file -e "$REMOTE_HEAD^{commit}"
git show -s --format='commit=%H%nparents=%P%nsubject=%s%ncommitted=%cI' "$REMOTE_HEAD"
git merge-base --is-ancestor "$STAGE1B" "$REMOTE_HEAD"
git diff --quiet HEAD "nexus-latest/$BRANCH"
git fsck --no-dangling --no-reflogs

gh pr view 6 --repo sree181/Nexus-latest \
  --json number,url,state,baseRefName,baseRefOid,headRefName,headRefOid,mergeCommit
gh pr checks 6 --repo sree181/Nexus-latest
```

At audit time, the application baseline was `9f832e7…`, a descendant of completed Stage 1B commit `ee2ebb…`. PR #6 (`feat(web): reconstruct all seven Figma workflows exactly`) was **open**, clean, and unmerged. Treat that as historical audit evidence only; the branch head and GitHub checks must be queried again when they matter. No successful signed-commit verification was established by the audit.

### 2.5 Start a safe feature branch

```bash
FEATURE=cursor/ABC-123-short-description

test -z "$(git status --porcelain)" || {
  echo 'Stop: working tree is not clean.' >&2
  false
}
BASE=$(git rev-parse HEAD)
test "$BASE" = "$(git rev-parse nexus-latest/manus/figma-exact-v3)"
git merge-base --is-ancestor ee2ebb372b1779cdea4522c762a4fc174c7ae2cc "$BASE"
git switch --create "$FEATURE" "$BASE"
git push --set-upstream nexus-latest "HEAD:refs/heads/$FEATURE"
git status --short --branch
```

Use small, reviewable commits. Never force-push shared work without an explicit team process.

---

## 3. System map

### 3.1 Runtime architecture

```text
                         Customer-managed TLS ingress
                                      |
      Browser (React/Vite SPA) -------+------ same-origin /auth and /api
              |                               |
              |                         Nginx web container
              |                          SPA fallback + proxy
              |                               |
              +------------------------- FastAPI control plane ------------------+
                                        |          |                              |
                                  OIDC/PKCE     Gateway seam                 SQLite control state
                                        |          |                              |
                                        |    +-----+----------------------+       |
                                        |    |                            |       |
                                   opaque HttpOnly                 SampleGateway  EngineGateway
                                   browser session                (dev/demo)    (engine mode)
                                                                        |            |
                                                                        |        HyperMesh stores
                                                                        |        + native graph/evidence
                                                                        |            |
 Cursor native hook ---> private local IPC ---> meshagent-recorder ---> encrypted local queue
        |                         |                    |                       |
        |                     same user,             v1 ordered             FastAPI recorder/
        |                     Cursor only            session delivery       package-gate APIs
        |
 repository-local opt-in (.meshagent.json: record=true)
```

### 3.2 Supported operating modes

| Mode | Activation | Identity and state | Appropriate use | Do not claim |
|---|---|---|---|---|
| **Local sample** | Default API configuration | `SampleGateway`; temporary state when `MESHAGENT_DB_DIR` is unset; asserted local identity may be available | UI development, fixtures, demonstrations | durable governed evidence, OIDC verification, production authorization |
| **Engine development** | `MESHAGENT_ENGINE=1`; use an isolated `MESHAGENT_DB_DIR` | `EngineGateway` with real engine stores; persistence survives restart only when a configured state directory is retained | integration testing, operator rehearsal | production readiness without OIDC/TLS/backups/operations controls |
| **Local Compose demo** | `docker compose up --build` | engine-enabled named Docker volume; HTTP localhost; API and web host ports published | single-host demo/integration work | production topology or exposure to untrusted networks |
| **Production single tenant** | standalone `docker-compose.production.yml`, engine, durable state, OIDC, customer TLS/ingress, strict audit, recorder signing key | `EngineGateway`; opaque HttpOnly browser sessions; API authorization and a single writer per state directory | server/control-plane deployment baseline | HA, managed service, external audit signing, production native-recorder rollout, Stage 1C packaging |

> **Production rule:** never use sample mode, a temporary/unset/relative `MESHAGENT_DB_DIR`, or locally asserted identity as production.

---

## 4. Directory map

All paths below are relative to repository root.

| Path | What belongs there | Cursor change guidance |
|---|---|---|
| `apps/web/` | React 18 + TypeScript Vite SPA, Nginx runtime image | Use typed API helpers; preserve routing, capability guards, responsive CSS boundaries |
| `apps/web/src/main.tsx` | web boot order and providers | Preserve CSS import order and provider nesting |
| `apps/web/src/router.tsx` | manually constructed TanStack Router tree | Update route declarations **and** links/navigation/tests for path or parameter changes |
| `apps/web/src/routes/` | role and workflow route components; six Figma route-local stylesheets | Keep route-specific workflow interpretation and desktop/mobile branches synchronized |
| `apps/web/src/components/` | shared layouts, guards, Figma primitives, navigation, project scope | Keep common primitives data-driven; do not move domain assertions into generic components |
| `apps/web/src/lib/api.ts` | centralized browser transport and API methods | Do not add ad hoc `fetch` calls in workflow routes |
| `apps/web/src/lib/auth.ts` | OIDC/local-mode browser identity headers and socket behavior | Preserve cookie-vs-asserted-local distinction |
| `apps/web/src/lib/queryClient.ts` | TanStack Query defaults | Do not casually raise retries/polling globally |
| `apps/web/src/lib/useIdentity.ts` | `Me` query and capability source | UI gates must derive from this API-returned identity |
| `apps/web/src/index.css` | UI token import, Tailwind, legacy/control/developer/workflow CSS | Broad selector or token changes can affect unrelated surfaces |
| `apps/web/src/figma-workflow-v3.css` | shared Figma v3 CSS tokens/primitives and attention styling | Preserve shared breakpoints, reduced-motion behavior, and `figma3-*` namespace |
| `packages/ui/` | small shared package: `Button`, `Badge`, `cn`, CSS tokens | Extend deliberately; it is not a full component system |
| `packages/graph/` | renderer-agnostic `GraphPayload` types, Graphology/Cytoscape transforms | Preserve native n-ary `Relation` records; use public exports |
| `services/api/app/` | FastAPI routes, auth, gateway, workflows, control plane, persistence | Keep route code at Gateway/API contracts, not raw engine calls |
| `services/api/tests/` | API/domain pytest suite | Put tests adjacent to the governing domain rather than generic endpoint-only files |
| `services/engine/` | HyperMesh Python layer, native core, agent memory, CodeGraph recorder | Do not import this directly from UI or API route handlers |
| `recorder/` | native Go daemon, hook, IPC, encrypted queue, DPoP/enrollment client | Native scope is Cursor-only; preserve causal delivery and privacy contracts |
| `adapters/cursor/` | Python compatibility hook and native hook configuration references | Python compatibility is direct HTTP and has different local-storage guarantees |
| `adapters/claude-code/` | Python direct-HTTP Claude Code compatibility adapter | Not a native recorder integration |
| `adapters/mcp/` | stdio JSON-RPC MCP server | Separate process and credential plane; not native daemon IPC |
| `scripts/demo/` | guarded demo/configuration helpers | Use configurator; do not overwrite hooks/config manually |
| `scripts/recorder/` | development native-recorder installers | Development tooling only, not Stage 1C packaging |
| `scripts/ops/` | backup, restore, verification, drill, upgrade, rollback handoffs | Deployment owns external service-manager/KMS/traffic hooks |
| `scripts/release/validate.sh` | comprehensive release validation | Do not call `SKIP_CONTAINERS=1` release-equivalent |
| `docker-compose.yml` | local Compose demo | Do not combine with production Compose |
| `docker-compose.production.yml` | standalone production baseline | Use this file alone with the `production` profile |
| `.cursorrules` | source-level architecture/change rules | Treat as mandatory project rules in Cursor |
| `.meshagent.json` | repository recorder privacy/gate configuration | Do not weaken exclusions or overwrite an opt-out |

---

## 5. Frontend stack, runtime, and routing

### 5.1 Boot path and build boundaries

`apps/web/src/main.tsx` performs this boot sequence:

```text
index.css -> figma-workflow-v3.css
  -> React.StrictMode
    -> QueryClientProvider(queryClient)
      -> SignInGate
        -> RouterProvider(router)
```

The router in `apps/web/src/router.tsx` is manually composed with `createRootRoute`/`createRoute`. There is **no file-route generator**. Route string or parameter renames therefore require coordinated updates to route declarations, `Link`/`navigate` calls, tests, and any API-derived `WorkItem.route` assumptions.

The Vite app aliases both workspace packages directly to their TypeScript source and deduplicates `react`/`react-dom`; `@meshagent/ui` and `@meshagent/graph` do not need separate prebuilds for normal web development.

Production chunks deliberately separate:

- Developer routes/components: `developer-workspace`;
- Cytoscape/D3: `graph-vendor`;
- TanStack: `tanstack-vendor`;
- React: `react-vendor`;
- remaining node modules: `vendor`.

Treat this chunking as a build/output boundary when adding dependencies.

### 5.2 Role shells and nested layouts

The root layout always renders `ModeBanner`.

- A **server-confirmed Developer** role uses `DeveloperProjectProvider`, `.dev-app`, and `DeveloperRail`.
- All other roles use the `Sidebar` in a flex shell.
- This is one frontend application with role-specific visual shells, not separate applications.
- `RoleLanding` opens a workspace only when the server identity is internally consistent (`role === primary_role`). It routes Developer to `/developer/sessions`, Analyst to `/analyst/queue`, CISO to `/ciso/overview`, and Platform Administrator to `/admin/onboarding/recorder`. Missing/inconsistent/failed identity is an error state, not a fallback workspace.

Nested parent routes own their shared chrome and context:

| Parent route | Owner/layout behavior |
|---|---|
| `/developer/sessions/$sessionId` | `DeveloperSessionLayout` fetches the chosen session; polls every 3 seconds only for `starting`, `active`, or `ending`; provides session context; owns Overview/Activity/Security/Evidence tab chrome |
| `/developer/connections` | `DeveloperConnectionsLayout` owns Overview/Editors/Repositories/Devices navigation |
| run layout | `RunLayout` owns run header/tab chrome for six children and validates rewind `?at=` as a positive integer |

`DeveloperProjectProvider` loads `api.developerSessions(200)`, derives distinct repositories, filters cached sessions in memory, and mirrors the selected project filter through `?project=` using `history.replaceState`. Routes calling `useDeveloperProject` must remain under this provider.

### 5.3 The seven Figma v3 workflow routes

Only the seven route components below import Figma v3 primitives. Keep their record wording, stage mapping, allowed actions, and evidence selection in the route because only the route knows what the returned record proves.

| Route component | URL | Router capability | Data / polling | Evidence source and action boundary | Route-local CSS |
|---|---|---:|---|---|---|
| `DeveloperAttention.tsx` | `/developer/attention` | `review.own` | `developerAttention(500)` plus `developerAttentionEvidence(attentionId)`; attention 8 s, scoped evidence 15 s | project-filtered attention; backend answers why/affected/outcome from the selected evaluation and native relation ULIDs; creates review using `session_id`, `policy_evaluation_id`, kind, and UI-required ≥20-character rationale; journey comes from returned review state | shared Figma v3 attention styles |
| `DeveloperReviewDetail.tsx` | `/developer/reviews/$requestId` | `review.own` | developer review/graph; record 8 s, graph 12 s | developer-scoped endpoints; requester view is read-only; next-step copy derives only from `ReviewRequest` state | `routes/developer-result.css` |
| `AnalystOperations.tsx` | `/analyst/queue` | `case.read` | work queue 10 s, notifications 15 s, selected evidence 15 s | review work uses review graph; case work uses review graph only when `finding_id` starts `review:`, otherwise run graph; bulk assignment uses per-item expected version | `routes/analyst-operations.css` |
| `AnalystReviewDetail.tsx` | `/analyst/reviews/$requestId` | `review.read` | review record 10 s, graph 15 s | Analyst endpoint can expose owner assignment/decision/escalation only where `review.write`/`case.write`; every mutation carries `version_counter` | `routes/analyst-review.css` |
| `AnalystCaseDetail.tsx` | `/analyst/cases/$caseId` | `case.read` | record and selected evidence as appropriate | review graph only for `review:` findings, otherwise run graph; assignment/transition uses item version; close/resolve needs disposition and evidence IDs | `routes/analyst-case.css` |
| `CisoApprovals.tsx` | `/ciso/approvals` | `exception.approve` | no audited primary-record polling interval | joins approvals/exceptions/policies client-side; native exception evidence is per selected exception; own requester, expired request, missing rationale, and stale version block decision | `routes/ciso-desk.css` |
| `CisoExceptionDetail.tsx` | `/ciso/exceptions/$exceptionId` | `exception.approve` | no audited primary-record polling interval | governance evidence endpoint; renewal/revoke only for active approved record, correct capability, and a different requester; payloads carry expected version | `routes/ciso-exception.css` |

**Figma route integrity rules**

1. A Developer Attention “View version steps” action scrolls to a returned explanation. It does **not** install a package, mutate a manifest, rerun a policy, or prove remediation.
2. A requester’s Developer Review page is read-only; no UI copy may imply the Developer decides the review.
3. An exception request does not decide itself. A different CISO makes approval, renewal, or revocation decisions.
4. A 404 evidence projection in the audited developer/analyst evidence views means **pending/empty projection**, not generic record failure and not a successful invented empty graph. Other evidence errors remain retryable failure states while the workflow record remains independently readable.
5. Use `StateFrame`, `ConflictRecovery`, and `IntegrityRef` patterns where already used rather than bypassing recovery/status semantics.
6. Treat any Figma Make export as a **design reference only**. Do not copy its mock data modules, fake endpoints, top-level demo router, browser role selector, or simulated workflow state into production. Recreate the composition with the existing typed queries, mutations, server capabilities, and real `GraphPayload` evidence.

### 5.4 Platform Administrator and recorder enrollment surfaces

Stage 1B adds a fourth, non-composable role: **Platform Administrator**. It is intentionally outside the seven Developer → Analyst → CISO workflow surfaces.

| Route | Component | Capability / authority | Data and behavior |
|---|---|---|---|
| `/admin/onboarding/recorder` | `routes/AdminOnboarding.tsx` | `recorder.admin.read` | polls `api.recorderOnboarding` every 15 seconds; presents identity-provider, signing, active, attention, and legacy readiness without developer content |
| `/admin/recorders` | `routes/AdminRecorders.tsx` | `recorder.admin.read` | polls the content-free recorder fleet every 15 seconds; filters all/attention/legacy; opens a selected recorder inspector |
| `/admin/recorders/$deviceId` | `routes/AdminRecorders.tsx` | `recorder.admin.read`; trust mutation additionally checks `recorder.trust.write` | shows trust, delivery, queue/config status and immutable trust receipts; quarantine/revoke requires a reason and `expected_version` |
| `/connect/recorder?code=…` | `routes/RecorderEnrollment.tsx` | server-authenticated **Developer owner**; API enforces approval authority | validates the one-time code, displays bounded device/key/grant facts, and approves recording/package-gate grants only |

`components/Sidebar.tsx` owns the Platform Administrator navigation. `routes/RoleLanding.tsx` sends this role to `/admin/onboarding/recorder`. Never expose prompts, source paths, repositories, commands, packages, sessions, Analyst cases, or CISO decisions in the Platform Administrator surfaces. Quarantine and revoke stop future trusted delivery; they do not delete the encrypted device queue. Re-enrollment creates a new device identity.

### 5.5 Shared Figma v3 primitives and responsive behavior

`apps/web/src/components/FigmaWorkflowV3.tsx` provides:

- `FigmaJourney`, with canonical stages: `detected`, `developer_action`, `security_review`, `investigation`, `ciso_decision`, `remediation`, `verified`;
- route-supplied stage overrides so the UI does not assert stages absent from the record;
- `FigmaEvidenceGraph`, a compact evidence projection;
- `FigmaResponsibilityDock` and `FigmaMobileDock`.

`FigmaEvidenceGraph` is intentionally **not** a complete graph renderer. It:

- excludes tombstoned nodes;
- ranks nodes for a selected `why`, `affected`, or `outcome` display lens; Developer Attention supplies that lens from the server-answer question and hides the component's legacy internal tabs;
- shows at most six nodes;
- uses direct edges when available, otherwise relation-member fallback edges;
- presents the first three readable relation rows plus a native `<details>` list for all relations;
- returns selected `GraphNode` objects to the route;
- supports keyboard selection with Enter/Space;
- omits SVG but retains textual evidence trails in `mobileEvidenceOnly` mode.

Figma responsiveness is CSS-driven. Both desktop and mobile JSX branches commonly exist and CSS controls visibility. `figma3-desktop-only` and `figma3-mobile-only` switch at 768 px; attention compresses at 1180 px; Developer Review has an additional 1080–769 px compact desktop column. `FigmaMobileDock` is sticky, includes safe-area padding, and actions generally need full-width, at least 44 px mobile targets. Shared Figma button transitions honor `prefers-reduced-motion`.

When changing route behavior, update **both** desktop/mobile branches and preserve shared data queries above display branches where possible.

### 5.6 CSS ownership

| Layer | Files | Responsibility |
|---|---|---|
| semantic tokens / Tailwind mapping | `packages/ui/src/tokens.css`, `apps/web/tailwind.config.ts` | base palette, typography, radius, shadow, semantic utility mapping |
| broad app / legacy / non-Figma controls | `apps/web/src/index.css` | Tailwind import plus legacy developer/workflow/control styling |
| shared Figma v3 | `apps/web/src/figma-workflow-v3.css` | `--figma3-*` tokens, shared primitives, attention layout |
| Figma route-local surfaces | six route CSS files named in the seven-route table | route-specific desktop/mobile refinements |

The audited aggregate of `index.css`, `figma-workflow-v3.css`, and six Figma route sheets was 3,994 lines. Do not introduce an unrelated third responsive system. Avoid broad CSS selector or token renames: `.workflow-*` and `.figma3-*` are overlapping visual vocabularies with different consumers.

Non-Figma responsive rules also matter:

- `DeveloperRail` becomes a fixed 252 px mobile drawer below 767 px.
- `Sidebar` becomes a focus-contained, Escape-dismissable dialog drawer below 767 px and uses `inert` when closed.
- Tables use horizontal scroll wrappers below 640 px rather than pseudo-table collapse.

---

## 6. Browser-to-API wiring and query conventions

### 6.1 Transport boundary

All browser HTTP calls belong in `apps/web/src/lib/api.ts`.

- `get`, `post`, and `delete` call **relative** `/api` paths.
- Requests use `credentials: "same-origin"` and `authHeaders()`.
- `post` JSON-encodes body and merges caller headers.
- Error responses become `ApiError(status, API detail or HTTP status)`.
- Only HTTP 401 invokes `handleUnauthorized`.
- Encode path variables with `encodeURIComponent` in client methods.

Do not add independent route-level `fetch` calls or direct engine requests. Nginx proxies `/auth/` and `/api/` internally to `api:8000`; Vite proxies `/auth` and `/api` to `VITE_API_BASE` or `http://localhost:8000`, including `/api` WebSocket proxying.

### 6.2 Primary screen-to-API trace

Methods below are defined in `apps/web/src/lib/api.ts`. The helper prepends `/api`, so a client path such as `/reviews/{id}` is publicly requested as `/api/reviews/{id}`.

| Browser surface | Typed client methods | Public FastAPI endpoints |
|---|---|---|
| Developer Attention | `developerAttention`, `developerAttentionEvidence`, `createDeveloperReviewRequest` | `GET /api/v1/developer/attention`; `GET /api/v1/developer/attention/{attentionId}/evidence`; `POST /api/v1/developer/review-requests` |
| Developer Review Result | `developerReviewRequest`, `developerReviewGraph` | `GET /api/v1/developer/review-requests/{requestId}`; `GET /api/v1/developer/review-requests/{requestId}/graph` |
| Analyst Operations | `workQueue`, `savedWorkViews`, `notifications`, `bulkAssignWork`, plus selected `reviewGraph` or `runGraph` | `GET /api/operations/work`; `GET/POST/DELETE /api/operations/views…`; `GET /api/notifications`; `POST /api/operations/work/bulk-assign`; selected evidence endpoint |
| Analyst Review | `review`, `reviewGraph`, `assignReview`, `decideReview`, `escalateReview` | `GET /api/reviews/{requestId}`; `GET /api/reviews/{requestId}/graph`; `POST /api/reviews/{requestId}/assign`; `POST /api/reviews/{requestId}/decision`; `POST /api/reviews/{requestId}/escalate` |
| Analyst Case | `case`, `assignCase`, `transitionCase`, `requestException`, selected `reviewGraph` or `runGraph` | `GET /api/cases/{caseId}`; `POST /api/cases/{caseId}/assign`; `POST /api/cases/{caseId}/transition`; `POST /api/exceptions`; selected evidence endpoint |
| CISO Decision Desk | `approvals`, `exceptions`, `policies`, `exceptionEvidence`, `decideApproval` | `GET /api/approvals`; `GET /api/exceptions`; `GET /api/policies`; `GET /api/exceptions/{exceptionId}/evidence`; `POST /api/approvals/{approvalId}/decision` |
| CISO Exception Detail | `exception`, `exceptionEvidence`, `renewException`, `revokeException` | `GET /api/exceptions/{exceptionId}`; `GET /api/exceptions/{exceptionId}/evidence`; `POST /api/exceptions/{exceptionId}/renew`; `POST /api/exceptions/{exceptionId}/revoke` |
| Platform Administrator onboarding/fleet | `recorderOnboarding`, `recorders`, `recorder`, `quarantineRecorder`, `revokeRecorder` | `GET /api/v2/recorders/onboarding`; `GET /api/v2/recorders`; `GET /api/v2/recorders/{deviceId}`; `POST /api/v2/recorders/{deviceId}/quarantine`; `POST /api/v2/recorders/{deviceId}/revoke` |
| Developer recorder approval | `pendingRecorderEnrollment`, `approveRecorderEnrollment` | `GET /api/v2/recorders/enrollments/{userCode}`; `POST /api/v2/recorders/enrollments/approve` |

For an end-to-end change, trace from route component → `api.ts` method → FastAPI route in `services/api/app/main.py` → domain store/service → `Gateway`/projection where applicable → Pydantic response → TanStack Query cache key → rendered state. Do not stop at the first layer that compiles.

### 6.3 Query rules

Default `QueryClient` behavior:

| Default | Value |
|---|---:|
| `staleTime` | 30,000 ms |
| `retry` | 1 |
| `refetchOnWindowFocus` | `false` |

`useIdentity()` uses query key `['me']`, calls `api.me`, has `staleTime: Infinity`, and `retry: false`.

Workflow polling is deliberate, not a generic default. Preserve the route cadence listed above. Use query keys consistently: on mutation, invalidate exact record keys and the relevant list/queue/notification/graph keys. When the server returns the updated record, routes may use `setQueryData` first; invalidation alone is not always immediate enough for visible state.

### 6.4 Optimistic concurrency

Never remove expected-version fields or silently retry a conflict.

| Domain | Field / expected-version source |
|---|---|
| analyst case assignment/transition | `CaseRecord.version` |
| analyst review actions | `ReviewRequest.version_counter` |
| bulk assignment | per-item `expected_version` |
| CISO approval decision | approval `expected_version` |
| exception renew/revoke | exception `expected_version` |
| destructive forget | `If-Match` preview version; production also requires `Idempotency-Key` |

HTTP 412 is recognized by `isVersionConflict`. `ConflictRecovery` directs the user to reload rather than silently overwriting newer state.

---

## 7. Identity, roles, capabilities, and authorization

### 7.1 Authentication modes

| Mode | Browser behavior | Security meaning |
|---|---|---|
| OIDC mode | When both `VITE_OIDC_ISSUER` and `VITE_OIDC_CLIENT_ID` are built into the web bundle, `SignInGate` checks `/auth/session` and starts API-owned OIDC/PKCE at `/auth/login` | browser tokens are opaque HttpOnly, same-origin cookies; API verifies identity and owns authorization |
| Local development mode | Without both Vite OIDC variables, `authHeaders` sends asserted `X-MeshAgent-User` / `X-MeshAgent-Role` values from `localStorage`; `socketProtocols` conveys `meshagent.local` identity | deliberately **unverified** development/presentation identity, never a production substitute |

Local identity defaults to `dev@localhost` / `developer`. `setLocalIdentity` writes local storage and uses `window.location.assign('/')`, so persona changes reload the SPA and can lose unsaved UI state. `Sidebar` and `DeveloperRail` expose local persona choices only when `me.verified === false` and include `platform_admin`; the standalone `LocalIdentityPicker` exposes only Developer/Analyst/CISO. Do not assume every identity entry point offers the same roles.

### 7.2 UI guards versus API enforcement

`RequiresCapability` calls `useIdentity()` before rendering a protected route. It displays loading/identity-verification failure without rendering the child, then checks API-returned capabilities. `RequiresAnalyst` specializes `fleet.read`.

> Client guards prevent misleading UI and premature protected requests. **They are not authorization enforcement.** The API remains the authority and must retain route/service-level checks.

### 7.3 Role/capability boundaries

| Primary role | Intended scope | Important limits |
|---|---|---|
| Developer | own runs, sessions, review requests, recorder/package gate | own non-seeded runs only; developer review endpoints are owner scoped; cannot decide own review |
| Analyst | fleet/evidence/case/review read and write; queue/triage; request/renew exceptions where authorized | does not own policy activation or CISO-only governance decisions |
| CISO | Analyst powers plus policy write/activate, exception approve/revoke, recommendations/remediations/reports and the legacy `device.fleet` capability | maker-checker and requester-separation rules still apply; this is **not** Stage 1B recorder trust administration |
| Platform Administrator | content-free recorder administration, device/trust/rollout operations | not a Developer-evidence or governance-decision impersonation role |

The exact authority source is `services/api/app/auth.py`; reusable FastAPI dependencies in `services/api/app/main.py` include Analyst/CISO/capability dependency patterns. Do not substitute a frontend role comparison for a server capability decision.

### 7.4 Owner isolation and privacy behavior

- `_may_read` in `services/api/app/main.py` allows a fleet reader, a deliberately seeded/public run, or a Developer whose subject matches `RunSummary.owner`; unauthorized Developer access is a 404 and audited access denial, not an information-revealing 403.
- Developer-session persistence requires owner equality through `DeveloperSessionStore._owned()` methods. Do not read a session by ID without owner scoping.
- Developer review endpoints are owner-filtered; Analyst/CISO review APIs use `review.read` / `review.write` capability boundaries.
- `owner = null` is **not** publication authorization. Only a seeded run is public to Developers.
- Device/recorder credentials are accepted only by recorder routes; human-only routes reject device tokens.

---

## 8. Backend modules and endpoint families

### 8.1 Backend control-plane map

| Location | Responsibility |
|---|---|
| `services/api/app/main.py` | FastAPI app, startup validation/lifespan reconcilers, CORS, route definitions, typed error mappings, dependencies |
| `services/api/app/auth.py` | OIDC/local identity handling and capability mapping |
| `services/api/app/gateway.py` | abstract `Gateway` seam and non-durable `SampleGateway` |
| `services/api/app/engine_gateway.py` | EngineGateway, native HyperMesh projection, graph/forget behavior |
| `services/api/app/engine_seed.py` | opens named HyperMesh `MemoryStore` directories under `MESHAGENT_DB_DIR` |
| `services/api/app/models.py` | core Pydantic run/graph/provenance/recorder/security/SBOM/device wire contracts |
| `services/api/app/developer_session_models.py` | strict connected-editor v1 protocol and activity-event contracts |
| `services/api/app/workflow_models.py` | case, policy, exception, approval, review, work, governance contracts |
| `services/api/app/developer_sessions.py` | durable ordered session SQLite store and migrations |
| `services/api/app/session_projection.py` | ordered SQLite-to-legacy-recorder/native evidence projection bookkeeping |
| `services/api/app/control_plane.py` | mutable workflow SQLite state, event records, notifications, views, projection outboxes |
| `services/api/app/review_service.py` | review lifecycle and evidence-graph perspective boundaries |
| `services/api/app/audit.py` | append-only fsynced JSONL audit hash chain |
| `services/api/app/operations.py` | atomic versioned destructive-operation journal |
| `services/api/app/registry.py` | atomic run metadata and deletion certificate registry |
| `services/api/app/paths.py` | state-path selection; temporary behavior outside configured durable state |

### 8.2 API endpoint families

This is a family map, not a substitute for FastAPI/OpenAPI response schemas. Consult `/openapi.json`, `models.py`, `developer_session_models.py`, and `workflow_models.py` before changing any payload.

| Family | Examples / purpose | Contract notes |
|---|---|---|
| browser auth and identity | `/auth/session`, `/auth/login`, `api.me` | API owns OIDC/PKCE and opaque browser sessions; production accepts no asserted local headers |
| health | `GET /api/health` | unauthenticated reachability/configuration only; inspect `gateway`, `durable`, `identity_provider`, not just HTTP 200 |
| runs and provenance | `/api/runs/{id}/graph`, `/why`, `/rewind`, `/forget/preview`, `/forget`, `/findings`, `/scan`, `/code`, `/sbom`, `/hypergraph`, `/decomposition`, `/scales` | graph result is `GraphPayload`; preserve `Relation`/tombstone semantics; owner isolation applies |
| developer sessions | `POST /api/v1/developer/sessions`; `POST /api/v1/developer/sessions/{sessionID}/events` | owner-scoped, strict ordered `meshagent.session.v1`, exact acknowledgement receipts |
| package gate | `POST /api/gate/package` | recorder gate decision; explicit validated `block` is the only deny result |
| developer reviews | developer-scoped review request and graph endpoints under `/v1/developer/review-requests` | requester-only evidence perspective; read-only developer view |
| analyst/CISO reviews and cases | review endpoints, queue, case operations | fleet capability scope; mutations require optimistic concurrency versions |
| policies/exceptions/approvals | policy lifecycle, exception request/approval/renewal/revocation, governance evidence | maker-checker/requester separation and digest-bound evidence |
| audit | `/api/audit` | security-office capability restricted |
| recorder enrollment/trust | `/api/v2/recorders/enrollments`, approval, token, config, heartbeat | DPoP/device recording credential plane; platform-admin views are content-free |

### 8.3 Gateway rule

`Gateway` is the required API seam.

```text
Web -> FastAPI route/service -> Gateway abstract contract
                              |-> SampleGateway (non-durable development/demo)
                              `-> EngineGateway (real HyperMesh evidence)
```

Route code must not query HyperMesh directly. To add engine behavior, extend the `Gateway` contract and **both** `SampleGateway` and `EngineGateway`, then keep sample-mode results honestly labeled. `EngineGateway` is the only API-side implementation that writes native HyperMesh evidence.

### 8.4 Wire-contract rule

The core graph wire contract exists in both places:

- `services/api/app/models.py` (Pydantic);
- `packages/graph/src/types.ts` (TypeScript).

Change them together, or generate TypeScript from `/openapi.json`. Preserve field names, literal unions, size limits, and response models. Frontend route code must consume the API’s `GraphPayload`, not Cytoscape internals or an invented UI-only graph.

---

## 9. HyperMesh, evidence, and durable workflow flow

### 9.1 Graph contract

`GraphPayload` contains nodes, rendered pairwise edges, and native n-ary relations:

```text
GraphPayload
  |- nodes: GraphNode[]
  |- edges: GraphEdge[]       (rendered pairwise projection)
  `- relations: Relation[]    (native n-ary fact)
```

`packages/graph` is renderer-agnostic and exports `GraphPayload`/types, `toGraphology`, `downstream`, `relationRows`, and `toCytoscape`.

- `relationRows` maps relation member IDs to current node labels for readable text.
- `toCytoscape` introduces an explicit relation node plus membership edges.
- Do **not** flatten an n-ary native relation into invented pairwise business facts.
- Preserve tombstoned/redacted status in provenance output.
- Cytoscape is an application dependency; Graphology belongs to the package contract.

### 9.2 Developer session: ledger before evidence

```text
Cursor/adapter observation
  -> FastAPI validates strict ordered session event
  -> developer-sessions.sqlite3 commits replay/order state
  -> durable activity receipt is returned
  -> session_projection records per-event projection state/retry delay
  -> EngineGateway / CodeGraphRecorder projects immutable native evidence
  -> run ID binds once projection is ready
```

`developer-sessions.sqlite3` uses WAL, `synchronous=FULL`, foreign keys, `BEGIN IMMEDIATE`, process locks, and numbered migrations. Its schema enforces owner+adapter+repository+source-session uniqueness, contiguous per-session sequence uniqueness, and source-event replay uniqueness. It is the ordering/replay authority; HyperMesh is the immutable evidence/analysis substrate.

`ActivityIngestReceipt` acknowledges durable SQLite acceptance/replay state (`accepted`, duplicates, projection state, `acknowledged_through`, next sequence, run ID). It does **not** promise that every record has already reached HyperMesh.

### 9.3 Mutable workflow control plane and native evidence projection

`control-plane.sqlite3` holds mutable aggregate state: cases, policies, exceptions, approvals, remediations, reports, reviews, lifecycle events, notifications, saved views, reconciler state, and evidence-projection outboxes. Native HyperMesh evidence is evidence of record; it is not mutable workflow state.

Review projection:

1. `create_review_request` freezes a minimum evidence snapshot and SHA-256 digest.
2. It uses the idempotency boundary `UNIQUE(owner_subject, policy_evaluation_id)`.
3. It writes a review event and `review_projection_outbox` in one transaction.
4. Reconciliation projects digest-bound, ordered review events into native relations.
5. Graph retrieval returns only sealed native evidence subgraphs; it does not rebuild evidence from UI state.

Governance projection similarly uses canonical snapshots, SHA-256 binding, deterministic `projection_id = event_id:relation_kind`, retry delays, source/digest re-verification, and refuses evidence output until required projections are native and valid.

> SQLite and HyperMesh writes are not one distributed transaction. Outboxes provide durable at-least-once delivery, deterministic IDs, retries, and digest/source checks. Clients must tolerate a workflow record whose native evidence is temporarily unavailable.

### 9.4 Agent memory and destructive operations

HyperMesh agent memory writes append-only `AGENT_MEMORY` hyperedges with an `edge:<ULID>` member, envelope metadata, content-sidecar SHA, and optional derivation edges. Stable entity names map through `agentmem_entities.db`.

Forget semantics are intentionally stronger than a hard delete:

- graph tombstones are written with actor attribution;
- content sidecars are redacted but retained hashes remain;
- graph/sidecar hash disagreement is rejected;
- interrupted graph-first redaction is recovered on read;
- redaction without tombstone fails closed;
- `why`, rewind/relearn, deletion certificates, closure snapshot, operations journal, audit write, and restart recovery are part of the contract.

For `forget`, use preview versioning (`If-Match`) and, in production, an `Idempotency-Key`. Do not add an alternate hard-delete route.

---

## 10. Native Cursor recorder and compatibility paths

### 10.1 Native Cursor Stage 1A flow

```text
Cursor event
  -> meshagent-hook (short-lived Go executable)
  -> private same-user local IPC
  -> meshagent-recorder daemon
  -> AES-256-GCM encrypted SQLite queue
  -> existing FastAPI v1 developer-session / package-gate APIs
  -> ordered HyperMesh projection
```

Relevant source paths:

- hook: `recorder/cmd/meshagent-hook/main.go`;
- daemon: `recorder/cmd/meshagent-recorder/main.go`;
- Cursor translation: `recorder/internal/cursorhook/processor.go`;
- IPC policy: `recorder/internal/ipc/protocol.go`;
- queue: `recorder/internal/cryptoqueue/`;
- sender/acknowledgements: `recorder/internal/service/sender.go`, `recorder/internal/protocol/types.go`.

The native IPC protocol accepts only `editor == "cursor"`. It is not a generic daemon service for Claude Code or MCP.

### 10.2 Event mapping and package gate

| Cursor event | Native behavior |
|---|---|
| first `beforeSubmitPrompt` | opens `meshagent.session.v1` at `POST /api/v1/developer/sessions` |
| later prompt | `prompt.submitted` |
| `afterFileEdit` | `file.changed`, including bounded on-disk file content when privacy/file-boundary checks succeed |
| `beforeShellExecution` | parses up to 100 prospective installs; calls `POST /api/gate/package`; records `policy.evaluated` only if a local session is open; returns a permission decision |
| `afterShellExecution` | `tool.completed` or `tool.failed`; emits `package.installed` only for exactly pinned successful installs |
| `sessionEnd` | `session.ended` |

The gate is deliberately fail-open for editor continuity:

- return `deny` only for a successfully parsed, validated verdict exactly equal to `block`;
- all other validated verdicts and gate/transport/decode failures return `allow`;
- normal observation/delivery failure never blocks editor work;
- a hook must return syntactically valid Cursor permission JSON even when local prerequisites fail.

Fail-open does **not** prove installation health. The setup script requires `meshagent-hook --health`; a harmless `allow` smoke response alone does not prove daemon/server delivery.

### 10.3 Privacy, local files, and queue behavior

Native recording requires a repository-local `<repo>/.meshagent.json` containing `"record": true`. Missing, malformed, false, or inaccessible configuration produces no recording. `exclude` matches repository-relative paths and basenames. `gate: false` disables refusals while preserving recording; no opt-in disables both recording and gate action.

Native capture canonicalizes repository/file paths, rejects outside paths and symlink/reparse escapes, and reads only regular UTF-8 in-bound files within `MESHAGENT_HOOK_MAX_BYTES` (default and ceiling: 200,000 bytes). Sensitive fields are bounded: prompts 4,096 runes, source 200,000 bytes, shell commands 4,096 runes, and bounded package/policy/tool values.

| Location | Native recorder content | Important boundary |
|---|---|---|
| `~/.meshagent/recorder.db` | SQLite WAL queue; encrypted payload bodies, plaintext operational metadata | not an entirely opaque encrypted database |
| `~/.meshagent/recorder.queue-key` | development queue key, mode 0600 | file-protected development key, not OS-backed key storage |
| `~/.meshagent/run/recorder.sock` (Unix) | same-user IPC socket | directory 0700/socket 0600; peer credential checks |
| `\\.\pipe\meshagent-recorder-<uid-hash>` (Windows) | current-user named pipe | current-user DACL |

Queue invariants include opener-before-activity persistence, contiguous transactional sequence allocation, oldest-per-session leasing, no newer bypass around a delayed head, exact acknowledgement before delete, 30-second lease replay after crash, rejection of new observation at global bounds without consuming sequence, atomic close/final activity, and encrypted retained blocked validation/trust failures.

Start response acknowledgement requires matching `id == envelope.SessionID` and `last_acked_sequence >= 1`; event acknowledgement requires `acknowledged_through >=` batch final sequence. Validation errors (400/413/415/422) and trust errors (401/403) block retained records; other failures back off exponentially from one second to 300 seconds. `meshagent-recorder replay` explicitly reattempts retained blocked records after remediation.

### 10.4 Safe repository configuration

Configure a **separate approved demo/work repository**, not this source checkout by accident. The configurator preserves non-meshAgent Cursor hooks, normalizes stale meshAgent entries, creates a timestamped backup before changing an existing `.cursor/hooks.json`, refuses explicit opt-outs, and can local-ignore generated setup.

```bash
cd /path/to/approved-demo-repository
export MESHAGENT_HOME=/path/to/meshagent-production-v1
"$MESHAGENT_HOME/scripts/demo/configure_cursor_hooks.py" "$PWD" \
  --native-hook "$HOME/.local/bin/meshagent-hook" \
  --local-exclude
# Fully quit and reopen Cursor after configuration changes.
```

The command requires the native daemon to be healthy before modifying hooks. It creates a default opt-in only where none exists:

```json
{
  "record": true,
  "exclude": ["secrets/*", "*.pem", "*.key", ".env", ".env.*"],
  "gate": true
}
```

Do not replace `.cursor/hooks.json` wholesale. Do not overwrite `.meshagent.json` that does not explicitly contain `record: true`; that is an intentional privacy choice. `--local-exclude` adds `.meshagent/`, `.meshagent.json`, and `.cursor/hooks.json` to `.git/info/exclude`; use it only for local demo setup, never to conceal an intentional reviewed repository configuration change.

### 10.5 Stage 1B and Stage 1C boundary

Stage 1B includes a development/demo enrollment flow with P-256 device keys, browser approval by the authenticated Developer, DPoP proof-of-possession, 10-minute recorder tokens scoped to `recorder.write gate.check`, signed configuration, content-free heartbeat, device trust/revoke behavior, and a content-free Platform Administrator plane.

However, checked-in native enterprise mode currently selects the **development software key adapter**. That adapter is exportable PEM-backed, and production rejects it. Current native enrollment/control-plane code is therefore an integrated development/demo workflow—not production native-recorder deployment readiness.

**Stage 1C is not implemented.** In particular, do not claim any of the following exist:

- Keychain/Secure Enclave, Windows CNG/TPM, Linux Secret Service/TPM2, or other OS-backed keystore adapters;
- verified platform attestation or nonce-bound real OS attestation;
- Apple Developer ID signing/notarization, Authenticode, signed Linux packages, SBOM/release provenance for distributed recorder packages, MDM deployment, or uninstall packages;
- a native Claude Code adapter, native MCP decision ingress, or a native Cursor/MCP reason-attribution bridge.

The development installers (`scripts/recorder/install-dev.sh`, `scripts/recorder/Install-Dev.ps1`) create per-user startup behavior only: LaunchAgent on macOS, user systemd service on Linux, or limited at-logon task on Windows. They are not enterprise-managed deployment mechanisms.

### 10.6 Compatibility paths

- `adapters/cursor/meshagent_hook.py` and `adapters/claude-code/meshagent_hook.py` are Python direct-to-HTTP compatibility adapters.
- They require repository `record: true`, deny only explicit gate `block`, and retain bounded process-locked JSONL queues under `~/.meshagent/queues/...`.
- Their queues are **not AES-GCM encrypted** and their file reading does not have the native Go no-follow/reparse protections.
- `adapters/mcp/meshagent_mcp.py` is stdio JSON-RPC, not native daemon IPC. Its write tools use a recording device token and its read tools require a separate delegated human/OIDC token (`MESHAGENT_READ_TOKEN`).
- The Python MCP explicit-module attribution bridge does not apply to the native Stage 1A Cursor queue path.

Before native installation, drain any legacy Python queue:

```bash
meshagent replay --json
# Continue only after retained is 0.
```

The native installer refuses a transition while nonempty legacy JSONL queue records exist; it will not silently import, encrypt, or discard them.

---

## 11. Development, Docker, and environment configuration

### 11.1 Toolchain baseline

| Tool | Declared/project minimum | Reproducible CI/container pin or audited use |
|---|---|---|
| Node | `>=20` | Node 22.13.0 for CI/web Docker |
| pnpm | `pnpm@9.12.0` | 9.12.0 |
| Python | project package `>=3.10`; README local API `3.12+` | API/CI/container Python 3.12.3 |
| Go recorder | module requirement | Go 1.25.13 for release command/toolchain |
| Docker | required for full container release validation | Docker Compose v2 compatible |

`.nvmrc` only says Node 20. A generic Node 20 may satisfy engines but is not byte-for-byte aligned with CI/container web builds.

### 11.2 Two-terminal local development

Terminal 1: sample API (no durable production claim):

```bash
cd services/api
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Terminal 2: Vite app:

```bash
cd /path/to/meshagent-production-v1
corepack enable
corepack prepare pnpm@9.12.0 --activate
pnpm install --frozen-lockfile
pnpm dev:web
```

Use the Vite URL (normally `http://localhost:5173`), FastAPI health at `http://localhost:8000/api/health`, and FastAPI docs at `http://localhost:8000/docs`.

### 11.3 Local real-engine integration

Use a disposable, isolated path—never production state:

```bash
cd /path/to/meshagent-production-v1
export MESHAGENT_ENGINE=1
export MESHAGENT_DB_DIR="$PWD/.local/meshagent-state"
cd services/api
PYTHONPATH=../engine uvicorn app.main:app --reload --port 8000
```

When `MESHAGENT_DB_DIR` is unset, the API creates a process-lifetime temporary directory and reports non-durable behavior. That is suitable for tests/sample work only.

### 11.4 Local Docker Compose

```bash
cd /path/to/meshagent-production-v1
docker compose up --build
curl -fsS http://localhost:8080/api/health | python3 -m json.tool
docker compose ps
```

Local Compose builds API/web from repository root, enables `MESHAGENT_ENGINE=1`, uses a named `meshagent-memory` volume at `/var/lib/meshagent`, publishes API `8000` and Nginx web `8080`, and uses HTTP localhost CORS/pairing configuration. It is a local convenience topology, not production.

To stop/start without deleting the named demo state:

```bash
docker compose stop
docker compose start
docker compose logs --tail=200 api web
```

Do **not** run `docker compose down -v` unless intentional state destruction is desired.

### 11.5 Production Compose baseline

Use the standalone file only—never layer it on top of `docker-compose.yml`:

```bash
cd /path/to/meshagent-production-v1
docker compose -f docker-compose.production.yml --profile production up --build -d
```

Production Compose:

- publishes only `web:${MESHAGENT_WEB_PORT}:8080`; the API has no host port;
- puts API and Nginx web containers on an internal bridge network;
- relies on Nginx proxying `/auth/` and `/api/` to `api:8000`;
- makes container filesystems read-only, drops all capabilities, enables `no-new-privileges`, and uses bounded `noexec,nosuid` tmpfs;
- mounts the single durable state volume at `/var/lib/meshagent`;
- reads the recorder signing key as a mode-0400 Docker secret, not as an image artifact or ordinary bind mount;
- requires a customer-managed TLS ingress before the published Nginx port.

Before production launch, use the deployment’s secret/configuration system to supply required values. Do not put them in a committed `.env`, shell history, tickets, or generated reports. Check rendering without printing resolved values where supported:

```bash
cd /path/to/meshagent-production-v1
docker compose -f docker-compose.production.yml --profile production config --quiet
```

On older Compose versions, redirect output to a protected location or `/dev/null`; unredirected `compose config` can print substituted values.

### 11.6 Important environment variables

This is a non-secret naming/reference table. Values shown are examples or placeholders only; obtain actual values from the deployment configuration system.

| Variable | Used by | Meaning / boundary |
|---|---|---|
| `MESHAGENT_ENV=production` | API | activates production validation; production rejects asserted local identity |
| `MESHAGENT_ENGINE=1` | API | selects `EngineGateway`; unset/default can select `SampleGateway` |
| `MESHAGENT_DB_DIR` | API | absolute, non-temporary durable state directory in production; one writer per directory |
| `MESHAGENT_OIDC_ISSUER` | API and web build | HTTPS issuer; enables verified OIDC mode |
| `MESHAGENT_OIDC_AUDIENCE` | API | API audience validation |
| `MESHAGENT_OIDC_CLIENT_ID` | API and Vite build | browser/client ID; public config but changing it requires rebuilding the web image |
| `MESHAGENT_OIDC_SCOPE` | API and Vite build | OIDC scopes; default `openid profile email` |
| `MESHAGENT_OIDC_ROLE_CLAIM` | API | signed claim used for role mapping; default `roles` |
| `MESHAGENT_ANALYST_GROUPS` | API | Analyst group mapping; must not overlap privileged mappings |
| `MESHAGENT_CISO_GROUPS` | API | CISO group mapping; must not overlap privileged mappings |
| `MESHAGENT_PLATFORM_ADMIN_GROUPS` | API | Platform Administrator mapping; required production Compose substitution |
| `MESHAGENT_DEPLOYMENT_ID` | API/recorder | server-owned recorder deployment boundary; required in production |
| `MESHAGENT_CORS_ORIGINS` | API | exact HTTPS public web origin membership; security/CSRF configuration |
| `MESHAGENT_WEB_URL` | API | canonical HTTPS public web origin; pairing, DPoP public-target, same-origin checks |
| `MESHAGENT_WEB_PORT` | production Compose | only published web port substitution |
| `MESHAGENT_MEMORY_VOLUME` | production Compose | durable Docker named-volume name; default `meshagent-memory` |
| `MESHAGENT_RECORDER_SIGNING_KEY_HOST_FILE` | Compose host only | host secret file used to create the Docker secret; do not commit or print it |
| `MESHAGENT_RECORDER_SIGNING_KEY_FILE` | API container | mounted in-container recorder signing-key path |
| `MESHAGENT_STRICT_AUDIT=true` | API | audit-write failures fail closed for protected operations |
| `MESHAGENT_GOVERNANCE_RECONCILE_SECONDS` | API | governance reconciler cadence; default 60 in production Compose |
| `VITE_API_BASE` | Vite dev only | optional API proxy target; default `http://localhost:8000` |
| `VITE_OIDC_ISSUER`, `VITE_OIDC_CLIENT_ID`, `VITE_OIDC_SCOPE` | web build | browser bundle config; runtime Nginx environment cannot change an already built bundle |
| `MESHAGENT_HOOK_MAX_BYTES` | native recorder | file capture maximum; default/ceiling 200,000 bytes |

Production startup requires engine, HTTPS issuer/audience/client/groups, non-overlapping privileged groups, deployment ID, an absolute non-temp database directory, strict audit, signing-key file, HTTPS `MESHAGENT_WEB_URL`, and exact HTTPS CORS including that origin.

The production public origin invariant matters for DPoP: the native manager signs request targets based on `MESHAGENT_API`, while server validation expects `MESHAGENT_WEB_URL + path`. Configure reverse proxy/external origin so these normalize consistently; test this as a rollout acceptance case.

### 11.7 Health interpretation

```bash
curl -fsS https://<public-web-origin>/api/health | python3 -m json.tool
```

For production acceptance require at least:

```text
status=ok
 gateway=EngineGateway
 durable=true
 identity_provider=true
```

A healthy endpoint proves reachability/configuration only. It does not prove OIDC reachability, correct role mapping, authorization, backup recovery, graph projection, recorder revocation handling, or responsive UI fidelity.

---

## 12. Data, persistence, secrets, and deployment boundaries

### 12.1 One state/recovery unit

All authoritative server state stays together under `MESHAGENT_DB_DIR` (production Compose: `/var/lib/meshagent` on one named volume):

| Artifact | Purpose |
|---|---|
| HyperMesh store directories (`run`, `run-<id>`, `fleet`, `governance`) | immutable native graph/evidence stores |
| `control-plane.sqlite3` | mutable workflows, events, notifications, views, outboxes/reconciler state |
| `developer-sessions.sqlite3` | ordered owner-scoped editor sessions, activity/replay/projection state |
| `browser_sessions.sqlite3` | API-owned browser session state |
| `agentmem_entities.db` and sidecars | stable native entity registry and content sidecars |
| `index.json` | run/deletion certificate registry |
| `operations.json` | versioned destructive-operation journal |
| `devices.json` | legacy device registration state |
| `recorder-control.sqlite3` | Stage 1B enrollment, DPoP replay, signed configuration, heartbeat, trust state, and recorder receipts |
| `audit.jsonl` | fsynced append-only hash-chained audit log |

Never split these artifacts across independently restored volumes; never restore a subset; never mount the same state to multiple API writers. Current locking is process-local (`RLock` plus SQLite transactional serialization), not a cross-process/distributed writer lease. Horizontal scaling requires a redesign or deployment-specific coordination/server database.

### 12.2 Audit and backup reality

`audit.jsonl` links entries by previous hash and SHA-256 digest. It detects modified/removed interior entries but is not independently tamper-proof against an actor who can rewrite the full file and recompute the chain. There is no external WORM/signature anchoring in scope.

Backup/restore scripts are intentionally deployment-neutral:

- `scripts/ops/backup.sh`, `restore.sh`, `verify-backup.sh`, `verify-state.sh`, `drill.sh`, `upgrade-preflight.sh`, `rollback.sh`;
- require deployment-owned absolute executable quiesce/resume hooks, unless an explicit offline acknowledgement proves every writer stopped;
- optional encryption/decryption hooks receive paths only;
- restore targets a new absolute empty directory, validates manifest, rejects tar traversal, avoids archive ownership restoration, then validates state/audit;
- deployment owns Docker/Kubernetes, KMS, ingress, traffic drain, and service-manager logic.

### 12.3 Secrets and data handling rules

- Use a secret/configuration platform for production configuration and signing/private-key material.
- Keep recorder signing key material in the Docker secret mechanism described by production Compose—not in images, ordinary source files, browser bundles, or reports.
- `OPENAI_API_KEY` is optional; absence is an intentionally labeled reference build. Enabling model/advisory feed integrations changes outbound data flows and needs deployment-owner approval.
- Do not stage credentials, keys, backups, customer data, raw governed-memory content, `.env` files, generated local state, or build artifacts. Never use `git add -f` to bypass ignore rules.
- Browser code never stores OIDC tokens; device recorder credentials and human OIDC credentials are separate planes.

---

## 13. Safely refine UI in Cursor

### 13.1 Before coding

1. Confirm clean branch/commit and open the exact files owning the route or primitive.
2. Identify server-owned truth: record state, capability, version field, evidence projection endpoint, and relevant query keys.
3. Confirm whether the change affects both desktop/mobile render branches or a shared Figma primitive.
4. Decide whether it is presentation-only, an existing-contract behavior change, or a new end-to-end feature.
5. Make the smallest typed change that preserves existing patterns.

### 13.2 Presentation-only Figma refinement recipe

Use this for spacing, hierarchy, responsive layout, evidence selection affordances, or clear state wording that does not change server behavior:

1. Start from the applicable component in `apps/web/src/routes/` and its route-local stylesheet.
2. Keep shared Figma behavior in `components/FigmaWorkflowV3.tsx` / `figma-workflow-v3.css`; keep record-specific language and stage mapping in the route.
3. Prefer `--figma3-*` and shared UI semantic tokens; do not hardcode a hex where a token exists.
4. Keep real semantic controls (`button`, `a`, labelled inputs); status must include words, not color alone.
5. Update both desktop/mobile JSX branches and test at 768 px plus any route-specific width (Developer Review 1080 px, Attention 1180 px).
6. Preserve keyboard node selection, `<details>` full relation list, sticky dock safe areas, and reduced-motion behavior.
7. Run focused web tests/typecheck/build and manually inspect target viewports.

### 13.3 Add a screen backed by new data

Follow the repository rule in `.cursorrules`:

```text
1. Add/change Pydantic contract in services/api/app/models.py (or the governing model file).
2. Mirror/change TypeScript graph/wire type in packages/graph/src/types.ts,
   or generate from /openapi.json.
3. Add Gateway method in services/api/app/gateway.py.
4. Implement it in both SampleGateway and EngineGateway.
5. Add FastAPI route and dependency/capability/owner checks in main.py or governing service.
6. Add a typed method in apps/web/src/lib/api.ts.
7. Build route component and add manual route declaration in apps/web/src/router.tsx.
8. Add links/navigation, queries/mutations, error/empty/conflict states, and tests.
```

If the route needs graph data, use `GraphPayload`; never couple screen logic to Cytoscape or fetch raw engine structures. If a relation is native n-ary evidence, retain it as a `Relation`, including tombstone/redaction status.

### 13.4 Add or change a mutation

1. Identify command ownership and the required FastAPI capability dependency.
2. Confirm owner isolation and maker-checker/requester separation in the service layer.
3. Use the appropriate `expected_version` / `version_counter`, or `If-Match` plus idempotency contract for forget.
4. Use `api.ts`; do not bypass credentials, auth headers, `ApiError`, or URL encoding.
5. On success, set returned exact record data where appropriate and invalidate exact record/list/queue/notification/graph query families.
6. Render 412 through `ConflictRecovery`; do not silently retry or overwrite.
7. Test success, forbidden/self-action, expired/stale/conflict, and projection-pending cases.

### 13.5 Add/change a graph renderer or evidence view

1. Keep `GraphPayload` as the input boundary.
2. Keep `Relation` facts n-ary; only renderer transformations may create visible relation nodes/membership edges.
3. Do not use the compact `FigmaEvidenceGraph` as the sole source for an action that needs complete evidence; preserve accessible full relation text/details or a full record link.
4. Distinguish 404 pending projection from other operational evidence errors exactly as existing route semantics do.
5. Do not synthesize a graph from workflow state when native projection is not ready.

---

## 14. Hard invariants

These are implementation constraints, not optional style preferences.

1. **API and engine boundary:** UI calls FastAPI only. API routes/services use `Gateway`; only `EngineGateway`/engine seed reaches HyperMesh.
2. **Contract parity:** API Pydantic types and TypeScript graph/wire types move together. Preserve strict bounds/literals/response models.
3. **Server authority:** client role/capability checks improve UX only. API authorization, owner isolation, and recorder machine authentication remain enforcement boundaries.
4. **No invented truth:** do not claim a policy verdict, completed workflow stage, code reachability, remediation, verification, graph edge, or deployment behavior without server-returned/native evidence. Sample data must be explicitly labeled sample.
5. **Graph honesty:** preserve native n-ary `Relation` records, tombstones, sidecar/integrity semantics, and complete relation text. Do not flatten semantics to fake pairwise facts.
6. **Ordering before evidence:** developer session durable SQLite ledger commits before native evidence projection; its receipt is not an evidence-completion promise.
7. **Outbox reality:** mutable workflow state and native evidence are not distributed transaction participants; tolerate pending projections and preserve digest/idempotence checks.
8. **Concurrency:** retain expected-version checks, 412 recovery, and forget `If-Match`/production idempotency behavior.
9. **Governance separation:** a requester cannot decide own exception; a policy author/submitter cannot independently activate in production when required; developer/review owner cannot decide own review; expired approvals do not show decision forms; rationale/evidence requirements remain.
10. **Privacy by opt-in:** native recording requires repository `record: true`; preserve exclusions and no-follow repository-file capture checks.
11. **Recorder continuity:** editor observation fails open; explicit validated `block` alone may deny a package gate; preserve queue causal ordering and exact acknowledgements.
12. **Durability scope:** all state under `MESHAGENT_DB_DIR` is one recovery/single-writer unit.
13. **Responsive parity:** Figma desktop/mobile branches both require behavior updates; use existing class/CSS system and accessible drawers/docks.
14. **Build boundaries:** keep Vite aliases/public package exports and intentional chunks intact; do not import non-existent compiled `dist` artifacts.

---

## 15. Do-not-do list

- Do not assume `origin` is the canonical remote; use `nexus-latest`.
- Do not use destructive Git commands to repair a dirty/divergent checkout.
- Do not modify repository source based on archived documentation without reconciling it with current authority/code.
- Do not add route-level ad hoc `fetch`, direct `/engine` access, or browser direct calls to HyperMesh.
- Do not implement an engine behavior in only `EngineGateway`; `SampleGateway` must remain coherent and honest.
- Do not use UI roles as authorization or remove server dependencies/service checks.
- Do not expose owner-scoped records with a generic lookup; apply existing owner/fleet rules and privacy-preserving 404 behavior.
- Do not silently retry HTTP 412, omit `expected_version`, or remove forget idempotency requirements.
- Do not replace a native relation with invented pairwise business facts or present a lossy evidence projection as complete proof.
- Do not represent 404 projection-pending as generic success or generic failure; retain route status-aware behavior.
- Do not claim that version navigation installed a package, a review resolved a remediation, or a stage is verified without a later recorded evaluation.
- Do not import Figma donor mock data, fake endpoints, demo routing, or simulated workflow outcomes into the production app.
- Do not overwrite `.cursor/hooks.json`, `.meshagent.json`, or an explicit recorder opt-out. Use the configurator.
- Do not treat a permission-hook `allow` smoke response as recorder connectivity proof.
- Do not silently migrate/delete legacy Python JSONL queues during native recorder setup.
- Do not claim Stage 1C, OS-backed recorder keys, attestation, package signing/notarization, MDM, native Claude support, native MCP IPC, or GitHub webhook/App support exists.
- Do not combine local and production Compose files, publish production API host ports, or set production public configuration only as runtime environment on an already built web image.
- Do not log resolved Compose secrets or commit `.env`, key, device, backup, state, model, or customer evidence artifacts.
- Do not treat an audit hash chain as independently immutable/external attestation.
- Do not mount one state directory to multiple API writers or restore only parts of it.
- Do not use `SKIP_CONTAINERS=1` for a release candidate.

---

## 16. Tests, validation, and release gates

### 16.1 Focused web validation

For a web/Figma change:

```bash
cd /path/to/meshagent-production-v1
corepack enable
pnpm install --frozen-lockfile
pnpm run check:web
pnpm run lint:web
pnpm run test:web
pnpm run build:web
```

For the canonical Figma primitive test specifically:

```bash
pnpm --filter @meshagent/web test -- FigmaWorkflowV3.test.tsx
git diff --check
git status --short
git diff --cached --check
```

At the audited reference commit, the web test command passed **15 test files / 40 tests**, and `pnpm --filter @meshagent/web typecheck` passed. This is a behavioral/unit baseline, not proof of live OIDC, API authorization, proxy deployment behavior, graph projection availability, or responsive visual fidelity.

Directly audited workflow test coverage includes:

| Direct coverage | Verified behavior examples |
|---|---|
| `apps/web/src/components/FigmaWorkflowV3.test.tsx` | seven journey stages, relation text, node selection |
| `apps/web/src/routes/CisoApprovals.test.tsx` | decision rationale and `expected_version` |
| `apps/web/src/routes/DeveloperReviewDetail.test.ts` | next-step helper behavior |
| `apps/web/src/routes/AnalystCaseDetail.test.ts` | `review:` finding parsing |

There are no audited direct test files for `DeveloperAttention`, `AnalystOperations`, `AnalystReviewDetail`, or `CisoExceptionDetail`; no audited browser/E2E visual breakpoint test covers all seven routes. Add focused tests when behavior changes and manually validate affected desktop/mobile branches.

### 16.2 API, engine, and recorder checks

```bash
cd /path/to/meshagent-production-v1

# API suite
PYTHONPATH=services/api:services/engine python3 -m pytest -q services/api/tests

# Native HyperMesh core tests
make -C services/engine/hypermesh_core clean native-test

# Go recorder validation
(
  cd recorder
  go test -race ./...
  go vet ./...
  GOTOOLCHAIN=go1.25.13 go run golang.org/x/vuln/cmd/govulncheck@v1.6.0 ./...
  go build ./cmd/meshagent-recorder ./cmd/meshagent-hook
)
```

Choose domain tests next to the contract you change. Common API suite locations include:

- `services/api/tests/test_developer_sessions.py`;
- `test_review_workflow.py`;
- `test_role_workflows.py`;
- `test_governance_enforcement.py`;
- `test_governance_projection.py`;
- `test_audit.py`;
- `test_operations.py` and `test_operation_recovery.py`;
- `test_content_integrity.py`.

### 16.3 Full release gate

```bash
cd /path/to/meshagent-production-v1
pnpm run validate:release
# Equivalent script command:
scripts/release/validate.sh
```

The intended complete gate covers locked frontend checks/tests/build, hash-locked API tests, native engine tests, Go race/vet/vulnerability/build checks, production Compose rendering, cold container image builds/checksums/artifact export, SBOM, OSV, Trivy filesystem/misconfiguration/secret scanning, and CI cross-build coverage.

`SKIP_CONTAINERS=1 scripts/release/validate.sh` is a workstation fallback only. It is **not** a release-candidate result.

**Known operational caveat:** the audited `scripts/release/validate.sh` exports issuer/audience/client ID, Analyst/CISO groups, CORS, web URL, and web port, but not all Compose-required substitutions (`MESHAGENT_PLATFORM_ADMIN_GROUPS`, `MESHAGENT_DEPLOYMENT_ID`, and `MESHAGENT_RECORDER_SIGNING_KEY_HOST_FILE`). In a clean shell, its production Compose render may fail unless those ambient values are supplied. CI supplies the missing variables. Fix or explicitly supply this gap before using the local container stage as release evidence.

### 16.4 Manual acceptance scenarios

Run these in a controlled local/integration environment when relevant; they supplement automated tests.

| Change area | Minimum acceptance scenario |
|---|---|
| role routing/auth | verified identity routes to correct landing; inconsistent identity opens no workspace; unverified local UI is clearly indicated |
| capability guard | protected child does not render before identity resolves; missing capability yields no protected request; API still rejects direct request |
| Developer workflow | project filtering uses `?project=`, review creation has an adequately long rationale, developer request remains read-only |
| Analyst queue/case | review versus run graph selection follows `finding_id` prefix; failed bulk item remains selected; 412 asks reload |
| CISO governance | own requester cannot decide/renew/revoke; expired approval has no decision form; rationale/version required |
| evidence | pending 404 projection and operational evidence failure render differently; relation text retains all member names |
| responsive UI | inspect desktop/mobile branches at <768 px and specific 1080/1180 px route boundaries; keyboard/focus/Escape drawer behavior works |
| local Compose | Nginx SPA deep link works; `/api/health` works through Nginx proxy; state persists after `stop`/`start` |
| production candidate | public HTTPS `/api/health` reports EngineGateway/durable/OIDC; authenticated role isolation, recorder revoke, backup/restore, and Nginx proxy behavior are tested |
| recorder | native health succeeds; opted-in repository captures bounded permitted events; explicit gate block denies; unavailable gate allows; blocked retained batches recover only after remediation/replay |

---

## 17. Troubleshooting

| Symptom | Likely cause | Safe diagnosis and next action |
|---|---|---|
| Branch or PR seems missing | `origin` points to another repository | Inspect `git remote -v`; use/add `nexus-latest` and fetch the exact branch; do not rewrite local work |
| Expected SHA check fails | branch advanced, wrong remote, shallow/stale checkout, or wrong checkout | Run `git ls-remote` against `sree181/Nexus-latest`, fetch the explicit ref, inspect commit/PR, then decide whether a new audited baseline is needed |
| Local branch switch/pull refuses | dirty files or divergent local branch | Stop. Commit/stash/use a worktree and inspect divergence. Do not `reset --hard` or force checkout |
| Web starts but API calls fail | API not running, wrong `VITE_API_BASE`, or Vite proxy issue | Check `curl http://localhost:8000/api/health`, terminal logs, and `apps/web/vite.config.ts`; browser calls should remain relative `/api` |
| Browser keeps redirecting/identity fails | mismatched OIDC build settings, cookie/origin issue, or invalid signed role mapping | Check API startup logs and `/api/health`; rebuild web image after Vite OIDC changes; verify exact HTTPS `MESHAGENT_WEB_URL` and CORS origin in production |
| Local persona switch not visible | verified OIDC identity is active or entry point has a narrower role list | Local persona controls appear only for unverified identity; use an appropriate local-mode entry point and expect a full SPA reload |
| Protected page flashes/request occurs before role validation | guard/query pattern was bypassed | Restore `RequiresCapability`/`useIdentity` flow; do not render child until identity/capability is established |
| Developer cannot read a run/session | intentional owner isolation | Confirm subject ownership, seeded status, and capability. Do not weaken 404 privacy behavior |
| Evidence panel is empty after workflow record loads | projection may be pending (404) or may have failed operationally | Preserve route-specific status handling; retry a real failure, do not synthesize an evidence graph |
| CISO/Analyst mutation gets 412 | optimistic version conflict | Reload/refetch, review current record, then reapply an intentional action with fresh expected version; do not auto-retry |
| New Figma CSS regresses another screen | global cascade or token/selector collision | Check `index.css` and Figma route/global layers; scope selectors to `figma3-*`/route class; inspect non-Figma workflow screens |
| Mobile layout is stale while desktop works | only one duplicated render branch was changed | Update both branches and test widths below 768 px plus route-specific 1080/1180 px rules |
| Native Cursor configurator fails health check | recorder daemon is not running/reachable or wrong hook path | Start the native daemon via approved development installation, run `meshagent-hook --health`, then rerun configuration; do not bypass health check by manually writing hooks |
| Native hook smoke returns `allow` but no records arrive | fail-open permission behavior is working but delivery/configuration is not proven | Check daemon `status`, repository `record:true`, IPC health, queue state, API/auth/trust status; `allow` alone is not evidence of delivery |
| Native installer refuses transition | retained legacy Python JSONL queue exists | Stop Cursor, run `meshagent replay --json`, remediate/drain until `retained: 0`; do not discard historical queue data |
| Queue batches are blocked | validation payload or recorder trust/revocation failure | Fix server/config/trust cause; run `meshagent-recorder replay`; preserved causal queue records are intentionally not hot-retried on 401/403 |
| Production Compose refuses to render | required variables/secret-file substitution absent | Supply all required configuration through deployment secret/config tools; use `config --quiet`; remember known release-script substitution gap |
| Production browser has wrong OIDC config after env change | public Vite variables are baked at build time | Rebuild/publish the web image with correct `VITE_OIDC_*` build arguments; changing Nginx runtime env cannot rewrite built JavaScript |
| DPoP proof target mismatch | public web URL and recorder API origin do not normalize to same expected target | Align/review `MESHAGENT_API`, external ingress, and `MESHAGENT_WEB_URL`; test through public origin rather than only internal service DNS |
| State vanished after Compose command | named volume was removed or temporary state used | Inspect volume/state mounts; never use `down -v` unless intentional; use a durable configured state directory/volume for relevant mode |
| Backup verification fails on fresh deployment | audit-required validation has no legitimate audit entry | Create/retain legitimate setup evidence, then repeat backup/verification; do not bypass audit requirements casually |

Useful native diagnostics:

```bash
meshagent-hook --health
meshagent-recorder status
meshagent-recorder replay
```

Useful Docker diagnostics:

```bash
docker compose ps
docker compose logs --tail=200 api web
curl -fsS http://localhost:8080/api/health | python3 -m json.tool
```

---

## 18. First-session checklist for Cursor

Use this checklist for a first engineering session in the repository.

### Repository and safety

- [ ] Clone/fetch from `https://github.com/sree181/Nexus-latest.git` using remote name `nexus-latest`.
- [ ] Fetch `nexus-latest/manus/figma-exact-v3`, verify local `HEAD` equals its current remote head, verify Stage 1B commit `ee2ebb…` is an ancestor, and record the exact current SHA in the task/PR.
- [ ] Confirm `git status --short` is empty before branching.
- [ ] Read `.cursorrules`, `README.md`, and the feature’s existing route/service/model tests before editing.
- [ ] Confirm the change is not directed by a `docs/archive/` document alone.
- [ ] Create a named feature branch from the verified commit; do not work directly on the canonical branch.

### Understand the affected contract

- [ ] Identify whether this is web-only presentation, a full-stack contract change, recorder behavior, or deployment/ops work.
- [ ] Locate the authoritative state/model: API Pydantic model, TypeScript type, Gateway method, route/service, query key, and test suite as applicable.
- [ ] Identify required capability, owner isolation, maker-checker/requester separation, and concurrency field before adding a mutation.
- [ ] Determine whether the feature consumes a `GraphPayload`; if so, retain `Relation`/tombstone truth and accessible complete relation details.
- [ ] For Figma screens, identify desktop/mobile render branches and route-local CSS file.

### Local development and validation

- [ ] Select the appropriate mode: sample UI work, isolated real-engine integration, local Compose demo, or a controlled production candidate—never conflate them.
- [ ] Run `corepack enable`, `pnpm install --frozen-lockfile`, and the focused web commands before/after web changes.
- [ ] Use a disposable `.local/meshagent-state` for real-engine local work, never a production state directory.
- [ ] Run focused tests first; run API/engine/recorder checks for touched domains; run full release gate for shared contracts/dependencies/release preparation.
- [ ] Run `git diff --check`, inspect staged diff, and confirm no secret/state/generated artifacts are staged.

### Cursor recorder work only

- [ ] Use an approved disposable Git repository for live recorder demonstrations.
- [ ] Confirm repository-local `.meshagent.json` explicitly says `record: true` and has appropriate exclusions.
- [ ] Run `meshagent-hook --health` before using native configuration.
- [ ] Drain legacy queue (`meshagent replay --json`, `retained: 0`) before native install.
- [ ] Use `scripts/demo/configure_cursor_hooks.py` rather than replacing hook JSON; fully restart Cursor after success.
- [ ] State honestly that native recorder enterprise/Stage 1B code remains development/demo-only and **Stage 1C is not implemented**.

---

## 19. Ready-to-paste Cursor master prompt

Paste this at the beginning of a Cursor task in this repository, then add the concrete feature request after the final line.

```text
You are working in the meshAgent monorepo. Follow repository rules in .cursorrules, read docs/CURSOR_ENGINEERING_GUIDE.md before editing, and treat README.md plus current production/demo documentation as authoritative; docs/archive is historical only.

Repository/source discipline
- The canonical audited remote is https://github.com/sree181/Nexus-latest.git. Use remote name nexus-latest; do not assume origin is correct.
- Canonical branch: manus/figma-exact-v3. Audited application baseline: 9f832e7b1bf75ce6b41d67e80ebedb3aad4fa7cc. Before editing, fetch nexus-latest, show git status/current branch/current SHA, verify local HEAD equals the freshly fetched canonical branch head, and verify Stage 1B commit ee2ebb372b1779cdea4522c762a4fc174c7ae2cc is an ancestor. Never use reset --hard, git clean -fd, forced checkout, or force-push to repair a dirty/divergent worktree.
- Create a small feature branch from the verified base. Do not commit secrets, .env files, keys, backup/state files, customer data, raw governed-memory content, or generated artifacts.

Architecture that must remain true
- Browser UI calls FastAPI only through apps/web/src/lib/api.ts. Use relative /api paths, same-origin credentials, authHeaders, ApiError semantics, and encoded path variables. Do not add ad hoc route-level fetches or direct engine calls.
- FastAPI reaches the engine only through services/api/app/gateway.py. Extend the Gateway interface plus BOTH SampleGateway and EngineGateway for new engine behavior. Do not import services/engine from UI or API route handlers.
- Wire contracts are mirrored: services/api/app/models.py and packages/graph/src/types.ts. Update both together (or generate from /openapi.json). Preserve strict Pydantic fields, literal unions, bounds, and response models.
- Graph screens consume GraphPayload. Preserve GraphPayload nodes, pairwise render edges, native n-ary Relation records, tombstone/redaction state, and relation-member text. Never flatten n-ary evidence into invented pairwise business facts or synthesize a graph from UI state.
- Client capability checks are UX gates only. API authorization, owner isolation, recorder machine authentication, maker-checker controls, requester separation, and optimistic concurrency are server enforcement boundaries.
- Preserve expected_version/version_counter semantics and 412 recovery. Never silently retry/overwrite conflicts. Forget retains If-Match and production Idempotency-Key behavior.
- Do not invent data or workflow facts. A developer review is read-only to its requester; version guidance does not install a package or prove remediation; a stage is not verified without later server-recorded evidence. Label sample data as sample.

Frontend/Figma rules
- Router is manual in apps/web/src/router.tsx: update declarations, links/navigate calls, tests, and path assumptions together.
- Keep Figma shared primitives data-driven in apps/web/src/components/FigmaWorkflowV3.tsx and record-specific stage mapping/wording/actions in the owning route.
- Treat Figma Make exports as visual references only. Never import donor mock data, fake endpoints, demo routing, role selectors, or simulated outcomes; wire the composition to existing typed API methods and returned GraphPayload facts.
- Figma desktop and mobile JSX branches are both present and CSS controls display. Update both branches, use existing figma3 CSS and route-local styles, and test under 768 px plus any route-specific 1080/1180 px breakpoint.
- Preserve accessibility: real semantic controls, labels, text status in addition to color, keyboard graph selection, full relation details, focus-contained/Escape mobile drawers, sticky dock safe areas, and reduced-motion behavior.
- Preserve Vite aliases/source workspace imports and manual chunk boundaries. Use public package exports; do not import imagined dist artifacts.

Identity, evidence, recorder, and deployment rules
- Keep OIDC cookie mode distinct from asserted unverified local mode. Do not portray local persona headers as verified login.
- Preserve developer owner scoping, Analyst/CISO capability boundaries, Platform Administrator content-free recorder scope, and privacy-preserving 404 behavior where established.
- Workflow state is mutable SQLite control-plane state; HyperMesh is immutable/digest-bound evidence. Outbox projection can be pending. Preserve route-specific handling of evidence 404 projection-pending versus retryable operational failures.
- Native recorder recording is repository-local opt-in: require .meshagent.json record:true, preserve exclusions and native file-boundary protections. Do not overwrite .cursor/hooks.json/.meshagent.json; use scripts/demo/configure_cursor_hooks.py.
- Native hook failure is intentionally fail-open except a successfully validated explicit package-gate block. Preserve encrypted queue ordering and exact acknowledgements.
- Do not claim OS-backed recorder keystores, attestation, signed/MDM installer packaging, GitHub App/webhook ingestion, native Claude support, or native MCP IPC. Stage 1C is not implemented.
- Do not combine local docker-compose.yml with docker-compose.production.yml. Production uses EngineGateway, one durable MESHAGENT_DB_DIR writer, OIDC, exact HTTPS public URL/CORS, customer TLS ingress, and secret-managed key material. Browser VITE OIDC config is build-time.

Working method
1. First inspect the relevant existing component/route/style, API model/client method, service/Gateway method, query keys, and nearest tests. State a short implementation plan and the invariants it preserves.
2. Make the smallest typed change consistent with existing patterns. Avoid new frameworks, global CSS changes, or unrelated refactors.
3. For mutations, implement capability/owner/version behavior end-to-end and invalidate/set the appropriate exact record, list, queue, notification, and graph query keys.
4. Add or update focused tests next to the governing domain. Run the appropriate checks:
   - web: pnpm install --frozen-lockfile && pnpm run check:web && pnpm run lint:web && pnpm run test:web && pnpm run build:web
   - API/engine/recorder when touched: the relevant pytest/native/Go checks
   - shared contracts, dependencies, or release work: pnpm run validate:release (SKIP_CONTAINERS=1 is not release evidence)
5. Manually verify affected error/empty/loading/conflict states and responsive desktop/mobile behavior. End with git diff --check and a concise summary of changed files, validation run, and remaining caveats.

Concrete task follows:
```

---

## 20. Final engineering posture

Make changes that are **small, typed, evidence-honest, capability-aligned, and recoverable**. The fastest safe path is normally to trace an existing vertical slice—model, gateway, route/service, `api.ts`, query/mutation, component/CSS, and test—then adapt it without bypassing the contracts described above.

When the task crosses a boundary (wire model, evidence semantics, authorization, durable state, recorder trust, deployment configuration, or shared CSS), slow down, enumerate the invariant, and validate the whole affected slice. This repository intentionally distinguishes convenient local/demo behavior from production controls; preserve that distinction in code, documentation, tests, and UI language.
