#!/usr/bin/env bash
set -euo pipefail

base="${1:-http://localhost:8080}"
base="${base%/}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'FAIL: required command not found: %s\n' "$1" >&2
    exit 1
  }
}

need curl
need python3

printf 'MeshAgent live-demo preflight\n'
printf '  origin: %s\n' "$base"

curl -fsS --max-time 10 "$base/" >"$tmp/index.html"
grep -qi '<title>MeshAgent' "$tmp/index.html" || {
  printf 'FAIL: web application did not return the MeshAgent document\n' >&2
  exit 1
}
printf '  PASS: web application\n'

curl -fsS --max-time 10 "$base/api/health" >"$tmp/health.json"
python3 - "$tmp/health.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    health = json.load(handle)
problems = []
if health.get("status") != "ok":
    problems.append(f"status={health.get('status')!r}")
if health.get("gateway") != "EngineGateway":
    problems.append(f"gateway={health.get('gateway')!r}")
if health.get("durable") is not True:
    problems.append("durable is not true")
if problems:
    raise SystemExit("FAIL: API health: " + ", ".join(problems))
print("  PASS: durable EngineGateway")
print("  identity provider: " + ("configured" if health.get("identity_provider") else "local demo identity"))
PY

for path in /developer/sessions /analyst/queue /ciso/overview; do
  curl -fsS --max-time 10 "$base$path" >"$tmp/page.html"
  grep -qi '<title>MeshAgent' "$tmp/page.html" || {
    printf 'FAIL: route did not return the SPA: %s\n' "$path" >&2
    exit 1
  }
  printf '  PASS: route %s\n' "$path"
done

if command -v meshagent >/dev/null 2>&1; then
  meshagent config get >/dev/null
  printf '  PASS: meshagent CLI installed\n'
else
  printf '  WARN: meshagent CLI is not installed in this shell\n'
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  printf '  PASS: Docker Compose available\n'
else
  printf '  WARN: Docker Compose was not available to this shell\n'
fi

printf 'READY: browser, durable API, and role routes are reachable.\n'
