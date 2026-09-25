# Live Client Demo Runbook

**Duration:** 25 minutes
**Flow:** Developer → Analyst → CISO → verified resolution
**Data rule:** Create a fresh session during the meeting. Do not present seeded records as client activity.

## 1. Demonstration objective

Prove that a developer can remain in Cursor while MeshAgent records an opted-in, repository-aware evidence trail; that Security receives one accountable work item; and that the CISO can make an independently controlled decision against the exact policy and evidence record.

## 2. Required preparation

Use the Mac setup guide to start MeshAgent in Engine mode and connect Cursor. Clone an approved test repository through normal Git. Create a disposable branch such as `demo/meshagent-client`. Confirm the repository has no secrets and that the client has approved the test prompt.

Run this immediately before the meeting:

```bash
scripts/demo/verify-live-demo.sh http://localhost:8080
meshagent doctor
meshagent sessions
```

The preflight must show `EngineGateway`, durable state, the three role routes, and the Cursor command path. If it does not, use the contingency section rather than improvising against an unhealthy service.

## 3. Presenter identities

| Role | Local demonstration identity | Purpose |
| --- | --- | --- |
| Developer | `maya@company.com` | Cursor session, package evidence, review request |
| Analyst | `priya@company.com` | Triage, ownership, collaboration, case, exception request |
| CISO | `alex@company.com` | Independent policy/exception decision and remediation oversight |

Local identity is appropriate only for the controlled demonstration. Explain once that production uses customer OpenID Connect and distinct Analyst/CISO groups.

## 4. Timed script

### Minutes 0–2: Set the boundary

Open the presentation and state: **Cursor is where the developer codes. MeshAgent is the governed control plane around that work.** Show the Git remote and disposable branch in the terminal. Do not claim pull-request or issue ingestion.

### Minutes 2–6: Developer works in Cursor

In Cursor, use a prompt that makes a small, understandable edit and requests one deliberately outdated package version. A suitable example is:

> Add a small HTTP client in `demo_client.py`, add one unit test, and install `requests==2.19.0`. Ask me before the installation command.

Allow the normal Cursor hooks to create the session and activities. Approve the package command only when ready to show the MeshAgent decision. The gate should record the package and ecosystem, retrieve advisory evidence when available, and present a safe-version path. MeshAgent is expected to fail open only when its control plane is unavailable; say so if asked.

Open **Developer → Sessions**, select the new repository session, then open **Security** or **Attention**. Show the exact package, advisories, fix guidance, linked code, and project context. Submit a review request for a safe version or temporary approval.

### Minutes 6–14: Analyst receives and owns the work

Switch to the Analyst identity and open **Security Operations**. Locate the new review by repository or package, assign it to Priya, set a due time, and add a short note. Open the evidence detail and point to the sealed package, advisory, code, and policy nodes. Escalate the review to a case when the UI offers that action.

From the case detail, request an exception. The request must be bound to the active policy version, source case, compensating control, expiry, and selected evidence identifiers. Use an owner value that describes the business owner, but explain that free-text owners are never treated as authenticated notification recipients.

### Minutes 14–21: CISO makes the governed decision

Switch to the CISO identity. Start at **Governance Command Center** and open **Decision Desk**. Show the exact policy version, policy digest, request digest, evidence, owner, controls, and expiry. Explain that the requester cannot approve, renew, or revoke the same request and that production policy activation requires a second CISO.

Approve or reject according to the rehearsal plan. Open the exception record and its native evidence timeline. Show that the decision, policy ancestry, requester, approver, and expiry are explicit records rather than labels inferred by the browser.

Open **Remediation Portfolio**. Create or open the linked remediation, assign an owner and due time, move it through the permitted lifecycle, and attach a verification evidence identifier before completion. A terminal outcome without evidence must be rejected.

### Minutes 21–23: Show proof

Open the policy or exception evidence panel. Point to the native record identifier, digest, relation vocabulary, and scoped ancestry. Explain the transactional outbox: workflow state and the projection request commit together; incomplete or digest-mismatched evidence is not served.

### Minutes 23–25: Resolve and return to engineering

Back in the approved test branch, replace the old dependency with the displayed safe version and run the focused test. Let Cursor record the clean package check. Return to the Developer review and show automatic verification when the clean evidence satisfies the request. Commit and push the disposable branch through normal Git if the client wants to see the repository result.

## 5. Expected proof points

| Proof point | Where to show it |
| --- | --- |
| Real Cursor activity | Developer session activity |
| Real Git context | Developer session project, branch, and repository |
| Package/advisory evidence | Developer Security or Attention |
| Immutable review evidence | Review detail and native evidence graph |
| Accountable work | Analyst queue owner, due time, notes, history |
| Exact policy decision | CISO Decision Desk and exception detail |
| Separation of duties | CISO decision context and rejected self-action if rehearsed |
| Verified remediation | Remediation Portfolio immutable events |
| Native provenance | Policy/exception HyperMesh evidence panel |

## 6. Contingency path

If Cursor cannot reach MeshAgent, do not create a second conflicting session. Show `meshagent doctor`, preserve the local queue, restore service, and run `meshagent retry`. If the advisory feed is unavailable, explain the recorded fail-open decision and continue with previously recorded evidence only if the client agrees. If any role route or API health check fails, stop the live mutation flow and use the validated screenshots in the deck; do not represent a broken interaction as successful.

## 7. Reset after the meeting

Stop the Compose stack, preserve the named volume only if the meeting record is authorized, delete the disposable Git branch if policy allows, revoke any demo device, and archive or destroy captured screenshots according to the meeting data plan.
