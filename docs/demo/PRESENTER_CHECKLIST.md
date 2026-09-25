# MeshAgent Presenter Checklist

## The day before

- [ ] Docker Desktop, Cursor, Git, Python 3, and the MeshAgent CLI are installed.
- [ ] The approved test repository is cloned and a disposable demo branch exists.
- [ ] Cursor project hooks point to the copied `adapters/cursor/meshagent_hook.py`.
- [ ] `docker compose up --build -d` completes.
- [ ] `scripts/demo/verify-live-demo.sh http://localhost:8080` reports READY.
- [ ] `meshagent doctor` confirms the API and device.
- [ ] Developer, Analyst, and CISO routes open in separate browser profiles or can be switched deliberately.
- [ ] The deliberately unsafe and safe package versions are known for the chosen ecosystem.
- [ ] No secrets, customer production credentials, or unapproved source are in the demo repository.
- [ ] The slide deck, runbook, and contingency screenshots are available offline.

## Ten minutes before

- [ ] Close unrelated applications and notifications.
- [ ] Verify the screen share shows only the approved display.
- [ ] Pull the approved repository and confirm the demo branch.
- [ ] Check `git status` and preserve any work that should not be changed.
- [ ] Open Developer Sessions, Analyst Security Operations, and CISO Command Center.
- [ ] Run the preflight again; do not start with a failing API, sample gateway, or non-durable state.

## During the demonstration

- [ ] Say that Cursor codes and MeshAgent governs.
- [ ] Create a fresh Cursor session; do not call seeded history live client activity.
- [ ] Show Git remote/branch/commit context without claiming PR or issue ingestion.
- [ ] Pause at the package gate and explain fail-open behavior honestly.
- [ ] Submit one Developer review request.
- [ ] Assign, note, and escalate it as Analyst.
- [ ] Bind the exception to policy, case, controls, expiry, and evidence.
- [ ] Use a different CISO identity for the decision.
- [ ] Show native evidence and a remediation outcome with verification proof.
- [ ] Return to the safe package and show verification.

## Do not overclaim

- [ ] Local identity is for the demo; production requires customer OpenID Connect.
- [ ] Version 1 is single-tenant and one-writer per state directory.
- [ ] GitHub v1 provides real Git context and normal push, not webhook ingestion.
- [ ] MeshAgent evidence supports governance decisions; it is not a regulatory attestation by itself.
