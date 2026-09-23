#!/usr/bin/env bash
# Perform an isolated restore drill; never points a running production service at the restored state.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: drill.sh --backup BACKUP_DIRECTORY [--work-root DIR] [--keep-restored] [--metadata-only]

By default MESHAGENT_DR_HEALTH_HOOK is required and receives: drill <restored-state-dir>.
It must start or query an isolated instance and exit nonzero unless the restored instance
passes its deployment health checks. --metadata-only validates the artifact and state but
is not an application recovery drill.
USAGE
}
bundle=""
work_root="${MESHAGENT_DRILL_ROOT:-${TMPDIR:-/tmp}}"
keep=0
metadata_only=0
while (($#)); do
  case "$1" in
    --backup) bundle="${2:-}"; shift 2 ;;
    --work-root) work_root="${2:-}"; shift 2 ;;
    --keep-restored) keep=1; shift ;;
    --metadata-only) metadata_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$bundle" ]] || die "--backup is required"
bundle="$(absolute_existing_dir "$bundle")"
work_root="$(absolute_dir_for_create "$work_root")"
if (( ! metadata_only )); then
  require_env MESHAGENT_DR_HEALTH_HOOK
  require_executable_hook "$MESHAGENT_DR_HEALTH_HOOK" "MESHAGENT_DR_HEALTH_HOOK"
fi
work="$(new_workdir "$work_root" meshagent-drill)"
target="$work/restored-state"
cleanup() {
  if ((keep)); then
    log "preserved drill workspace: $work"
  else
    rm -rf -- "$work"
  fi
}
trap cleanup EXIT
started="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
"$SCRIPT_DIR/restore.sh" --backup "$bundle" --target "$target" --require-audit
if ((metadata_only)); then
  result="metadata-and-state-validated"
  warn "metadata-only mode did not validate application recovery"
else
  log "invoking isolated application health hook"
  "$MESHAGENT_DR_HEALTH_HOOK" drill "$target"
  result="application-recovery-validated"
fi
cat > "$work/drill-report.env" <<EOF
DRILL_STARTED_AT=$started
DRILL_FINISHED_AT=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
BACKUP=$(basename -- "$bundle")
RESULT=$result
RESTORED_STATE=$target
EOF
chmod 600 -- "$work/drill-report.env"
log "disaster-recovery drill succeeded: $result"
