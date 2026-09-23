#!/usr/bin/env bash
# Create a quiesced, checksummed MeshAgent state backup.
# Hooks receive: <operation> <MESHAGENT_DB_DIR>. Encryption receives: <input> <output>.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: backup.sh [--source DIR] [--backup-root DIR] [--label LABEL] [--offline]

Defaults: --source uses $MESHAGENT_DB_DIR; --backup-root uses $MESHAGENT_BACKUP_ROOT.
By default, MESHAGENT_QUIESCE_HOOK and MESHAGENT_RESUME_HOOK must name absolute,
executable deployment hooks. Use --offline only after stopping every MeshAgent writer
and set MESHAGENT_BACKUP_OFFLINE_ACK=I_HAVE_STOPPED_ALL_MESHAGENT_WRITERS.
Optional MESHAGENT_ENCRYPT_HOOK receives <payload.tar> <payload.tar.enc>.
USAGE
}

source_dir="${MESHAGENT_DB_DIR:-}"
backup_root="${MESHAGENT_BACKUP_ROOT:-}"
label="${MESHAGENT_BACKUP_LABEL:-state}"
offline=0
while (($#)); do
  case "$1" in
    --source) source_dir="${2:-}"; shift 2 ;;
    --backup-root) backup_root="${2:-}"; shift 2 ;;
    --label) label="${2:-}"; shift 2 ;;
    --offline) offline=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$source_dir" ]] || die "state source is required (--source or MESHAGENT_DB_DIR)"
[[ -n "$backup_root" ]] || die "backup root is required (--backup-root or MESHAGENT_BACKUP_ROOT)"
safe_label "$label"
source_dir="$(absolute_existing_dir "$source_dir")"
backup_root="$(absolute_dir_for_create "$backup_root")"
[[ "$source_dir" != "$backup_root" ]] || die "state source and backup root must be different"
is_descendant "$backup_root" "$source_dir" && die "backup root must not be inside the state source"
require_cmd tar
require_cmd sha256sum
require_cmd flock

exec 9>"$backup_root/.backup.lock"
flock -n 9 || die "another backup or retention operation holds $backup_root/.backup.lock"

quiesced=0
resume() {
  if ((quiesced)); then
    log "resuming MeshAgent writers"
    if ! run_hook "$MESHAGENT_RESUME_HOOK" backup "$source_dir"; then
      log "ERROR: resume hook failed; investigate service availability immediately"
      return 1
    fi
  fi
}
trap resume EXIT

if ((offline)); then
  [[ "${MESHAGENT_BACKUP_OFFLINE_ACK:-}" == "I_HAVE_STOPPED_ALL_MESHAGENT_WRITERS" ]] \
    || die "--offline requires MESHAGENT_BACKUP_OFFLINE_ACK=I_HAVE_STOPPED_ALL_MESHAGENT_WRITERS"
  log "using operator-attested offline consistency; no quiesce hook will run"
else
  require_env MESHAGENT_QUIESCE_HOOK
  require_env MESHAGENT_RESUME_HOOK
  require_executable_hook "$MESHAGENT_QUIESCE_HOOK" "MESHAGENT_QUIESCE_HOOK"
  require_executable_hook "$MESHAGENT_RESUME_HOOK" "MESHAGENT_RESUME_HOOK"
  log "quiescing MeshAgent writers"
  run_hook "$MESHAGENT_QUIESCE_HOOK" backup "$source_dir"
  quiesced=1
fi

created="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
id="$(date -u +'%Y%m%dT%H%M%SZ')-$label"
[[ ! -e "$backup_root/$id" ]] || die "backup ID already exists: $id"
stage="$(new_workdir "$backup_root" meshagent-backup)"
bundle="$stage/$id"
mkdir -p -- "$bundle"
cleanup_stage() { rm -rf -- "$stage"; }
trap 'cleanup_stage; resume' EXIT

log "creating state archive $id"
tar --create --file "$bundle/payload.tar" --format=pax --numeric-owner --acls --xattrs -C "$source_dir" .
[[ -s "$bundle/payload.tar" ]] || die "state archive is empty"
chmod 600 -- "$bundle/payload.tar"
payload="payload.tar"
encrypted=0
if [[ -n "${MESHAGENT_ENCRYPT_HOOK:-}" ]]; then
  require_executable_hook "$MESHAGENT_ENCRYPT_HOOK" "MESHAGENT_ENCRYPT_HOOK"
  log "encrypting backup payload with the configured hook"
  "$MESHAGENT_ENCRYPT_HOOK" "$bundle/payload.tar" "$bundle/payload.tar.enc"
  [[ -s "$bundle/payload.tar.enc" ]] || die "encryption hook did not create a non-empty encrypted payload"
  chmod 600 -- "$bundle/payload.tar.enc"
  rm -f -- "$bundle/payload.tar"
  payload="payload.tar.enc"
  encrypted=1
fi
cat > "$bundle/metadata.env" <<EOF
BACKUP_FORMAT=meshagent-state-v1
CREATED_AT=$created
PAYLOAD=$payload
ENCRYPTED=$encrypted
RELEASE=${MESHAGENT_RELEASE:-unknown}
CONSISTENCY=$( ((offline)) && printf 'operator-attested-offline' || printf 'hook-quiesced' )
SOURCE_BASENAME=$(basename -- "$source_dir")
EOF
chmod 600 -- "$bundle/metadata.env"
write_manifest "$bundle"
# The final rename is atomic only within the configured backup filesystem.
mv -- "$bundle" "$backup_root/$id"
rm -rf -- "$stage"
trap - EXIT
resume
quiesced=0
log "backup complete: $backup_root/$id"
log "next: $SCRIPT_DIR/verify-backup.sh --backup $backup_root/$id"
