#!/usr/bin/env bash
# Gate an upgrade on a recent restorable backup and the candidate deployment's observable safety checks.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: upgrade-preflight.sh --candidate-api-url URL --state-dir DIR --backup BACKUP_DIRECTORY [--expected-version VERSION]

The candidate endpoint should be isolated from production traffic. Preflight requires a
valid backup and state, then requires /api/health to report engine mode, durable state,
and an enabled identity provider. This script does not deploy an image or change state.
USAGE
}
url=""
state="${MESHAGENT_DB_DIR:-}"
bundle=""
expected=""
while (($#)); do
  case "$1" in
    --candidate-api-url) url="${2:-}"; shift 2 ;;
    --state-dir) state="${2:-}"; shift 2 ;;
    --backup) bundle="${2:-}"; shift 2 ;;
    --expected-version) expected="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$url" && -n "$state" && -n "$bundle" ]] || die "--candidate-api-url, --state-dir, and --backup are required"
state="$(absolute_existing_dir "$state")"
bundle="$(absolute_existing_dir "$bundle")"
require_cmd curl
require_cmd python3
"$SCRIPT_DIR/verify-state.sh" --state-dir "$state" --require-audit
"$SCRIPT_DIR/verify-backup.sh" --backup "$bundle" --require-audit
body="$(curl --fail --show-error --silent --connect-timeout 5 --max-time 15 "$url/api/health")" \
  || die "candidate API health endpoint did not respond successfully: $url/api/health"
printf '%s' "$body" | python3 -c 'import json, sys
expected = sys.argv[1]
try:
    health = json.load(sys.stdin)
except json.JSONDecodeError as exc:
    raise SystemExit(f"candidate health response is not JSON: {exc}")
problems = []
if health.get("status") != "ok": problems.append("status is not ok")
if health.get("gateway") != "EngineGateway" or not health.get("mode", {}).get("engine"): problems.append("engine mode is not active")
if health.get("durable") is not True: problems.append("durable state is not reported")
if health.get("identity_provider") is not True: problems.append("OIDC identity provider is not reported")
version = health.get("version")
if expected and version != expected: problems.append(f"candidate version {version!r} does not match {expected!r}")
if problems:
    raise SystemExit("candidate rejected: " + "; ".join(problems))
print("candidate_health_version=" + str(health.get("version")))' "$expected"
log "upgrade preflight passed; capture the output and proceed through the release checklist"
