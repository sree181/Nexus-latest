#!/usr/bin/env bash
# Decryption hook for restore.sh and verify-backup.sh. Keep identities outside the repository.
set -euo pipefail
IFS=$'\n\t'
umask 077

usage() {
  cat <<'USAGE'
Usage: MESHAGENT_BACKUP_AGE_IDENTITY_FILE=/secure/path/key.txt decrypt-age.sh INPUT OUTPUT

The private identity path must be absolute, be a regular file, and should be supplied by
the recovery environment rather than stored with the backup or repository.
USAGE
}
[[ ${1:-} != "-h" && ${1:-} != "--help" ]] || { usage; exit 0; }
[[ $# -eq 2 ]] || { usage >&2; exit 2; }
[[ -n "${MESHAGENT_BACKUP_AGE_IDENTITY_FILE:-}" ]] || { echo "MESHAGENT_BACKUP_AGE_IDENTITY_FILE is required" >&2; exit 2; }
[[ "$MESHAGENT_BACKUP_AGE_IDENTITY_FILE" == /* && -f "$MESHAGENT_BACKUP_AGE_IDENTITY_FILE" && ! -L "$MESHAGENT_BACKUP_AGE_IDENTITY_FILE" ]] || { echo "identity file must be an absolute regular file" >&2; exit 2; }
command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 127; }
input="$1"
output="$2"
[[ -f "$input" && ! -L "$input" ]] || { echo "input must be a regular file" >&2; exit 2; }
mkdir -p -- "$(dirname -- "$output")"
tmp="${output}.tmp.$$"
trap 'rm -f -- "$tmp"' EXIT
age --decrypt --identity "$MESHAGENT_BACKUP_AGE_IDENTITY_FILE" --output "$tmp" "$input"
mv -- "$tmp" "$output"
trap - EXIT
