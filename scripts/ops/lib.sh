#!/usr/bin/env bash
# Shared helpers for MeshAgent operations scripts. Source this file; do not run it.
set -euo pipefail
IFS=$'\n\t'
umask 077

OPS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

log() { printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
warn() { log "WARNING: $*"; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }
require_env() { local name="$1"; [[ -n "${!name:-}" ]] || die "required environment variable is unset: $name"; }

absolute_existing_dir() {
  local path="$1"
  [[ -d "$path" ]] || die "directory does not exist: $path"
  realpath -e -- "$path"
}

absolute_dir_for_create() {
  local path="$1"
  mkdir -p -- "$path"
  realpath -e -- "$path"
}

safe_label() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die "label must be 1-64 characters of letters, digits, dot, underscore, or hyphen"
}

safe_filename() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "unsafe filename in backup metadata: $1"
}

is_descendant() {
  # $1 candidate, $2 parent. Both must be canonical absolute paths.
  [[ "$1" == "$2" || "$1" == "$2"/* ]]
}

require_executable_hook() {
  local hook="$1" name="$2"
  [[ "$hook" == /* ]] || die "$name must be an absolute path; do not pass a shell command string"
  [[ -f "$hook" && -x "$hook" ]] || die "$name is not an executable regular file: $hook"
}

run_hook() {
  local hook="$1"; shift
  require_executable_hook "$hook" "hook"
  "$hook" "$@"
}

write_manifest() {
  local bundle="$1"
  (
    cd -- "$bundle"
    find . -type f ! -name MANIFEST.sha256 -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum -- > MANIFEST.sha256
  )
  [[ -s "$bundle/MANIFEST.sha256" ]] || die "refused to create an empty checksum manifest"
  chmod 600 -- "$bundle/MANIFEST.sha256"
}

verify_manifest() {
  local bundle="$1"
  [[ -f "$bundle/MANIFEST.sha256" ]] || die "checksum manifest is missing: $bundle/MANIFEST.sha256"
  (
    cd -- "$bundle"
    sha256sum --check --strict MANIFEST.sha256
  )
}

metadata_value() {
  local metadata="$1" wanted="$2" value
  [[ -f "$metadata" ]] || die "backup metadata is missing: $metadata"
  value="$(awk -F= -v key="$wanted" '$1 == key { print substr($0, length(key) + 2); found=1; exit } END { if (!found) exit 1 }' "$metadata")" \
    || die "backup metadata has no $wanted entry"
  printf '%s' "$value"
}

validate_tar_members() {
  local payload="$1" member
  require_cmd tar
  while IFS= read -r member; do
    [[ -n "$member" ]] || continue
    [[ "$member" != /* ]] || die "backup tar contains an absolute path: $member"
    [[ "$member" != ../* && "$member" != */../* && "$member" != .. ]] || die "backup tar contains parent traversal: $member"
  done < <(tar --list --file "$payload")
}

new_workdir() {
  local parent="$1" prefix="$2"
  mkdir -p -- "$parent"
  mktemp -d "$parent/.${prefix}.XXXXXX"
}

latest_backup_dir() {
  local root
  root="$(absolute_existing_dir "$1")"
  find "$root" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z-*' -printf '%f\n' | LC_ALL=C sort | tail -n 1
}

restore_payload_to() (
  # $1 bundle, $2 clean target. MESHAGENT_DECRYPT_HOOK is required only for encrypted backups.
  local bundle="$1" target="$2" metadata payload encrypted scratch source_payload
  metadata="$bundle/metadata.env"
  payload="$(metadata_value "$metadata" PAYLOAD)"
  encrypted="$(metadata_value "$metadata" ENCRYPTED)"
  safe_filename "$payload"
  [[ -f "$bundle/$payload" ]] || die "backup payload is missing: $bundle/$payload"
  scratch="$(new_workdir "${TMPDIR:-/tmp}" meshagent-restore-payload)"
  source_payload="$bundle/$payload"
  cleanup_restore_payload() { rm -rf -- "$scratch"; }
  trap cleanup_restore_payload EXIT
  if [[ "$encrypted" == "1" ]]; then
    require_env MESHAGENT_DECRYPT_HOOK
    require_executable_hook "$MESHAGENT_DECRYPT_HOOK" "MESHAGENT_DECRYPT_HOOK"
    source_payload="$scratch/payload.tar"
    "$MESHAGENT_DECRYPT_HOOK" "$bundle/$payload" "$source_payload"
    [[ -s "$source_payload" ]] || die "decrypt hook did not create a non-empty payload"
  elif [[ "$encrypted" != "0" ]]; then
    die "unsupported ENCRYPTED value in metadata: $encrypted"
  fi
  validate_tar_members "$source_payload"
  tar --extract --file "$source_payload" --directory "$target" --no-same-owner --no-same-permissions
)

assert_clean_restore_target() {
  local target="$1"
  [[ "$target" == /* ]] || die "restore target must be an absolute path"
  if [[ -e "$target" || -L "$target" ]]; then
    [[ -d "$target" && ! -L "$target" ]] || die "restore target is not a real directory: $target"
    [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] || die "restore target is not empty; refusing to overwrite: $target"
  else
    mkdir -p -- "$target"
  fi
}
