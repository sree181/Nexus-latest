#!/usr/bin/env bash
# Restore a verified MeshAgent backup only into an empty, explicitly named directory.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: restore.sh --backup BACKUP_DIRECTORY --target ABSOLUTE_EMPTY_DIRECTORY [--require-audit]

The target is created if absent, but the script never empties or overwrites it. For an
encrypted backup, set MESHAGENT_DECRYPT_HOOK to an absolute executable that accepts
<encrypted-input> <decrypted-output>.
USAGE
}
bundle=""
target=""
require_audit=0
while (($#)); do
  case "$1" in
    --backup) bundle="${2:-}"; shift 2 ;;
    --target) target="${2:-}"; shift 2 ;;
    --require-audit) require_audit=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$bundle" && -n "$target" ]] || die "--backup and --target are required"
bundle="$(absolute_existing_dir "$bundle")"
assert_clean_restore_target "$target"
target="$(absolute_existing_dir "$target")"
args=(--backup "$bundle")
((require_audit)) && args+=(--require-audit)
"$SCRIPT_DIR/verify-backup.sh" "${args[@]}"
restore_payload_to "$bundle" "$target"
state_args=(--state-dir "$target")
((require_audit)) && state_args+=(--require-audit)
"$SCRIPT_DIR/verify-state.sh" "${state_args[@]}"
log "restore complete: $target"
log "Do not point a production service at this state until the disaster-recovery runbook's application health verification succeeds."
