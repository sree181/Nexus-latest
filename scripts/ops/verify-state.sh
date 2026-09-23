#!/usr/bin/env bash
# Verify the portable control-plane records that MeshAgent stores under MESHAGENT_DB_DIR.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() { echo "Usage: verify-state.sh [--state-dir DIR] [--require-audit]"; }
state_dir="${MESHAGENT_DB_DIR:-}"
require_audit=0
while (($#)); do
  case "$1" in
    --state-dir) state_dir="${2:-}"; shift 2 ;;
    --require-audit) require_audit=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$state_dir" ]] || die "state directory is required (--state-dir or MESHAGENT_DB_DIR)"
state_dir="$(absolute_existing_dir "$state_dir")"
require_cmd python3

python3 - "$state_dir" "$require_audit" <<'PY'
import hashlib
import json
import os
import sys

base, require_audit = sys.argv[1], sys.argv[2] == "1"
errors, warnings = [], []
index = os.path.join(base, "index.json")
audit = os.path.join(base, "audit.jsonl")
if os.path.exists(index):
    try:
        with open(index, encoding="utf-8") as fh:
            body = json.load(fh)
        if body.get("version") != 1:
            errors.append("index.json has an unsupported or missing version")
        if not isinstance(body.get("runs", []), list):
            errors.append("index.json runs is not a list")
        if not isinstance(body.get("certificates", []), list):
            errors.append("index.json certificates is not a list")
        for row in body.get("runs", []):
            if not isinstance(row, dict) or not isinstance(row.get("store"), str) or not row["store"]:
                errors.append("index.json contains a run without a usable store name")
                continue
            target = os.path.realpath(os.path.join(base, row["store"]))
            if os.path.commonpath([base, target]) != base:
                errors.append(f"index.json run store escapes state directory: {row['store']}")
            elif not os.path.isdir(target):
                warnings.append(f"indexed store is not present: {row['store']}")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"index.json cannot be read as JSON: {exc}")
else:
    warnings.append("index.json is absent; a newly initialized state may not have recorded runs yet")

if os.path.exists(audit):
    previous = "0" * 64
    try:
        with open(audit, encoding="utf-8") as fh:
            lines = list(fh)
        for lineno, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
                digest = item.pop("digest")
                calculated = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if item.get("prev") != previous or digest != calculated:
                    errors.append(f"audit.jsonl hash chain breaks at line {lineno}")
                    break
                previous = digest
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append(f"audit.jsonl is invalid at line {lineno}: {exc}")
                break
        print(f"audit_entries={len(lines)}")
    except OSError as exc:
        errors.append(f"audit.jsonl cannot be read: {exc}")
else:
    message = "audit.jsonl is absent"
    (errors if require_audit else warnings).append(message)

for warning in warnings:
    print(f"WARNING: {warning}", file=sys.stderr)
for error in errors:
    print(f"ERROR: {error}", file=sys.stderr)
if errors:
    sys.exit(1)
print("state verification passed")
PY
