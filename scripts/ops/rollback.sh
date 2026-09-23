#!/usr/bin/env bash
# Restore a known-good backup and hand off activation to deployment-owned hooks.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: rollback.sh --backup BACKUP_DIRECTORY --target ABSOLUTE_EMPTY_DIRECTORY --release RELEASE

MESHAGENT_ROLLBACK_ACTIVATE_HOOK is required and receives:
  rollback <restored-state-dir> <release>
It must perform the deployment-specific traffic drain, service stop, release activation,
and atomic state cutover. Optional MESHAGENT_ROLLBACK_HEALTH_HOOK receives the same
arguments and must prove the recovered deployment is healthy. This script does not delete
the previous state or release.
USAGE
}
bundle=""
target=""
release=""
while (($#)); do
  case "$1" in
    --backup) bundle="${2:-}"; shift 2 ;;
    --target) target="${2:-}"; shift 2 ;;
    --release) release="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$bundle" && -n "$target" && -n "$release" ]] || die "--backup, --target, and --release are required"
safe_label "$release"
require_env MESHAGENT_ROLLBACK_ACTIVATE_HOOK
require_executable_hook "$MESHAGENT_ROLLBACK_ACTIVATE_HOOK" "MESHAGENT_ROLLBACK_ACTIVATE_HOOK"
if [[ -n "${MESHAGENT_ROLLBACK_HEALTH_HOOK:-}" ]]; then
  require_executable_hook "$MESHAGENT_ROLLBACK_HEALTH_HOOK" "MESHAGENT_ROLLBACK_HEALTH_HOOK"
fi
"$SCRIPT_DIR/restore.sh" --backup "$bundle" --target "$target" --require-audit
log "calling deployment activation hook for release $release"
"$MESHAGENT_ROLLBACK_ACTIVATE_HOOK" rollback "$(absolute_existing_dir "$target")" "$release"
if [[ -n "${MESHAGENT_ROLLBACK_HEALTH_HOOK:-}" ]]; then
  log "calling rollback health hook"
  "$MESHAGENT_ROLLBACK_HEALTH_HOOK" rollback "$(absolute_existing_dir "$target")" "$release"
else
  warn "no MESHAGENT_ROLLBACK_HEALTH_HOOK configured; perform and record post-cutover health checks manually"
fi
log "rollback handoff completed; retain the replaced release and state until incident command closes the change"
