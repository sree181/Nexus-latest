#!/usr/bin/env bash
# Verify a MeshAgent backup manifest, payload readability, and portable state evidence.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() { echo "Usage: verify-backup.sh --backup BACKUP_DIRECTORY [--require-audit]"; }
bundle=""
require_audit=0
while (($#)); do
  case "$1" in
    --backup) bundle="${2:-}"; shift 2 ;;
    --require-audit) require_audit=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$bundle" ]] || die "--backup is required"
bundle="$(absolute_existing_dir "$bundle")"
[[ -f "$bundle/metadata.env" ]] || die "not a MeshAgent backup (metadata.env missing)"
format="$(metadata_value "$bundle/metadata.env" BACKUP_FORMAT)"
[[ "$format" == "meshagent-state-v1" ]] || die "unsupported backup format: $format"
log "verifying checksum manifest"
verify_manifest "$bundle"
work="$(new_workdir "${TMPDIR:-/tmp}" meshagent-backup-verify)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
state="$work/state"
mkdir -p -- "$state"
restore_payload_to "$bundle" "$state"
args=(--state-dir "$state")
((require_audit)) && args+=(--require-audit)
"$SCRIPT_DIR/verify-state.sh" "${args[@]}"
log "backup verification passed: $bundle"
