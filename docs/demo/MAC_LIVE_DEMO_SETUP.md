# MeshAgent Mac Setup for a Live Cursor and GitHub Demo

**Purpose:** Run the real EngineGateway locally, connect a real GitHub clone to Cursor, and generate new Developer, Analyst, and CISO records during the presentation.

## 1. What this setup proves

This setup does not use the sample task runner. Cursor hooks send real prompt, file, shell, package, and session events from the repository you open. MeshAgent derives the repository name, Git remote, branch, and commit from that local clone. The security gate checks an exact package version before a supported install command executes. The resulting Developer session, Analyst work, CISO decision, remediation history, and HyperMesh relations persist in the Docker volume.

MeshAgent does **not** currently ingest GitHub pull-request or issue webhooks. GitHub participates through the real cloned repository and its Git metadata; Cursor remains the coding agent. A normal `git push` proves the edited branch is a real GitHub branch, while MeshAgent proves what the connected agent did and how the organization handled the resulting security evidence.

## 2. Prerequisites

Install Docker Desktop, Git, Python 3.10 or newer, Cursor, and optionally GitHub CLI. Start Docker Desktop before continuing.

```bash
git --version
python3 --version
docker version
docker compose version
```

## 3. Clone and start MeshAgent

Use the release branch supplied with the demo package. From Terminal:

```bash
git clone https://github.com/sree181/Nexus-latest.git meshagent
cd meshagent
git switch manus/priority5-complete-demo-v1

docker compose up --build -d
```

Wait until the API is healthy, then open `http://localhost:8080`.

```bash
curl -fsS http://localhost:8080/api/health | python3 -m json.tool
docker compose ps
```

The health payload must report `"status": "ok"`, `"gateway": "EngineGateway"`, and `"durable": true`. Local role switching is intentionally visible in this development configuration. It is not the production identity path.

Run the packaged read-only preflight at any time:

```bash
scripts/demo/verify-live-demo.sh http://localhost:8080
```

## 4. Install and pair the recorder

Install the CLI in a dedicated virtual environment so the Cursor hook can import its shared protocol and state modules.

```bash
python3 -m venv "$HOME/.venvs/meshagent"
source "$HOME/.venvs/meshagent/bin/activate"
python3 -m pip install -U pip
python3 -m pip install "$PWD"

meshagent config set http://localhost:8080
meshagent login --label "Client demo Mac"
```

The login command prints a browser URL and a short code. Open that URL, use the **Developer** identity, approve the code in Connections → Devices, and return to Terminal. Then verify the recorder:

```bash
meshagent whoami
meshagent doctor
```

`meshagent doctor` must report a durable EngineGateway and no local permission or queue failures.

## 5. Connect a real GitHub repository to Cursor

Use a repository that you are allowed to modify. A small private demonstration repository is safer than a production repository.

```bash
cd "$HOME/Documents"
gh repo clone YOUR_ORG/YOUR_DEMO_REPOSITORY
cd YOUR_DEMO_REPOSITORY
git switch -c meshagent-live-demo
```

Copy the installed hook script into the demonstration repository. `MESHAGENT_HOME` is the local MeshAgent clone from section 3.

```bash
export MESHAGENT_HOME="$HOME/path/to/meshagent"
mkdir -p .meshagent .cursor
cp "$MESHAGENT_HOME/adapters/cursor/meshagent_hook.py" .meshagent/cursor_hook.py
cat > .meshagent.json <<'JSON'
{
  "record": true,
  "exclude": ["secrets/*", "*.pem", ".env", ".env.*"],
  "gate": true
}
JSON
```

Create or merge `.cursor/hooks.json`. Keep any existing Cursor hooks and add these MeshAgent commands under the corresponding event names:

```json
{
  "version": 1,
  "hooks": {
    "beforeSubmitPrompt": [{"type":"command","command":"python3 ./.meshagent/cursor_hook.py","timeout":5}],
    "afterFileEdit": [{"type":"command","command":"python3 ./.meshagent/cursor_hook.py","timeout":5}],
    "beforeShellExecution": [{"type":"command","command":"python3 ./.meshagent/cursor_hook.py","timeout":6,"failClosed":false}],
    "afterShellExecution": [{"type":"command","command":"python3 ./.meshagent/cursor_hook.py","timeout":5}],
    "sessionEnd": [{"type":"command","command":"python3 ./.meshagent/cursor_hook.py","timeout":10}]
  }
}
```

Open this repository folder in Cursor and restart Cursor after changing hooks.

## 6. Generate real evidence

Start a fresh Cursor conversation. Ask Cursor to make a small, reviewable change that imports a package. Then ask it to run one supported, exactly pinned install command. For a deterministic security handoff, use a deliberately old package only in an isolated demo environment:

```text
Create a small Python HTTP client in src/demo_client.py and then run pip install requests==2.19.0.
```

The package command is evaluated before execution. If the policy blocks it, that block is the expected result; do not bypass it. Continue by asking Cursor to update the code to the recommended safe version or by requesting Security review in MeshAgent.

Verify the new live session:

```bash
meshagent status
meshagent replay
meshagent doctor
```

In MeshAgent, switch to Developer and open **Sessions**. The newest row should show the real repository, Cursor adapter, branch, ordered activity, package decision, and HyperMesh evidence.

## 7. Complete the cross-role flow

1. **Developer:** Open the package item in **Attention** and create a Security review request.
2. **Analyst:** Switch to Analyst, open **Security operations**, assign the review, set a due date, add a note, and escalate it to a case if remediation needs tracking.
3. **Analyst:** In the case, request a policy exception only if the fix cannot be completed before the deadline. The UI automatically binds the case evidence identifier.
4. **CISO:** Switch to CISO, open **Decision desk**, inspect the exact policy version, request digest, controls, owner, expiry, and linked case, then approve or reject with a rationale.
5. **CISO:** Create remediation for the case, start it, and record a terminal outcome only with evidence identifiers.
6. **Developer:** Return to Cursor, update to the safe package version, rerun the gate, and end the Cursor session.
7. **Analyst/CISO:** Confirm the review verification, work history, policy/exception timeline, remediation event history, and native HyperMesh evidence.
8. **GitHub:** Commit and push the demo branch normally. Show the Git remote and commit in MeshAgent and the resulting branch on GitHub.

## 8. Stop and resume

```bash
# Preserve the Docker volume and stop services.
docker compose stop

# Resume later with the same state.
docker compose start

# Inspect logs if a page is unavailable.
docker compose logs --tail=200 api web
```

Do not run `docker compose down -v` unless you intentionally want to delete the local demonstration state.

## 9. Production boundary

The local demo uses an asserted role selector. A client deployment must use the documented OpenID Connect configuration, Transport Layer Security ingress, controlled persistent storage, encrypted backups, monitoring, and deployment acceptance tests. The product supports one writer process per state directory in v1.

## References

[1]: ../../adapters/cursor/README.md "MeshAgent Cursor recorder contract"
[2]: ../DEVELOPER_SESSION_BACKEND.md "Developer session backend and adapter contract"
[3]: ../PRODUCTION_OPERATIONS.md "MeshAgent production operations"
[4]: ../../README.md "MeshAgent production v1 architecture and startup"
