#!/usr/bin/env bash
# Encryption hook for backup.sh. Requires age and a public recipient, never a private key.
set -euo pipefail
IFS=$'\n\t'
umask 077

usage() {
  cat <<'USAGE'
Usage: MESHAGENT_BACKUP_AGE_RECIPIENT=age1... encrypt-age.sh INPUT OUTPUT

This hook encrypts a backup payload to the supplied age recipient. Keep the matching
private identity in an independently controlled recovery system. Test decryption
regularly with an explicitly configured MESHAGENT_DECRYPT_HOOK.
USAGE
}
[[ ${1:-} != "-h" && ${1:-} != "--help" ]] || { usage; exit 0; }
[[ $# -eq 2 ]] || { usage >&2; exit 2; }
[[ -n "${MESHAGENT_BACKUP_AGE_RECIPIENT:-}" ]] || { echo "MESHAGENT_BACKUP_AGE_RECIPIENT is required" >&2; exit 2; }
command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 127; }
input="$1"
output="$2"
[[ -f "$input" && ! -L "$input" ]] || { echo "input must be a regular file" >&2; exit 2; }
[[ "$output" != "$input" ]] || { echo "input and output must differ" >&2; exit 2; }
mkdir -p -- "$(dirname -- "$output")"
tmp="${output}.tmp.$$"
trap 'rm -f -- "$tmp"' EXIT
age --encrypt --recipient "$MESHAGENT_BACKUP_AGE_RECIPIENT" --output "$tmp" "$input"
mv -- "$tmp" "$output"
trap - EXIT
