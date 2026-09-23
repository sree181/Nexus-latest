#!/usr/bin/env bash
# Apply backup retention only after listing candidates and checking manifests.
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'USAGE'
Usage: retention.sh --backup-root DIR [--keep-days N] [--keep-count N] [--hold-file FILE] [--apply]

The default is a dry run. A non-empty hold file contains one backup directory ID per
line; held backup IDs are never selected. --apply verifies each candidate's checksum
manifest before deletion but does not decrypt it. Use a separately governed hold file.
USAGE
}
root="${MESHAGENT_BACKUP_ROOT:-}"
keep_days="${MESHAGENT_RETENTION_DAYS:-35}"
keep_count="${MESHAGENT_RETENTION_COUNT:-7}"
hold_file="${MESHAGENT_LEGAL_HOLD_FILE:-}"
apply=0
while (($#)); do
  case "$1" in
    --backup-root) root="${2:-}"; shift 2 ;;
    --keep-days) keep_days="${2:-}"; shift 2 ;;
    --keep-count) keep_count="${2:-}"; shift 2 ;;
    --hold-file) hold_file="${2:-}"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$root" ]] || die "backup root is required"
[[ "$keep_days" =~ ^[0-9]+$ && "$keep_count" =~ ^[0-9]+$ ]] || die "retention values must be non-negative integers"
root="$(absolute_existing_dir "$root")"
require_cmd flock
exec 9>"$root/.backup.lock"
flock -n 9 || die "another backup or retention operation holds $root/.backup.lock"

plan="$(mktemp "${TMPDIR:-/tmp}/meshagent-retention-plan.XXXXXX")"
chmod 600 -- "$plan"
trap 'rm -f -- "$plan"' EXIT
python3 - "$root" "$keep_days" "$keep_count" "$hold_file" <<'PY' > "$plan"
import datetime as dt
import os
import re
import sys
root, keep_days, keep_count, hold_file = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
held = set()
if hold_file:
    with open(hold_file, encoding="utf-8") as fh:
        held = {line.strip() for line in fh if line.strip() and not line.lstrip().startswith("#")}
pat = re.compile(r"^(\d{8}T\d{6}Z)-[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
now = dt.datetime.now(dt.timezone.utc)
records = []
for name in os.listdir(root):
    match = pat.fullmatch(name)
    path = os.path.join(root, name)
    if not match or not os.path.isdir(path) or os.path.islink(path):
        continue
    try:
        when = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        continue
    records.append((when, name))
records.sort(reverse=True)
for pos, (when, name) in enumerate(records):
    age = (now - when).days
    keep = name in held or pos < keep_count or age < keep_days
    reason = "legal-hold" if name in held else ("newest-count" if pos < keep_count else ("within-age" if age < keep_days else "expired"))
    print(("KEEP" if keep else "DELETE"), name, age, reason, sep="\t")
PY
while IFS=$'\t' read -r action name age reason; do
  printf '%-7s %s age=%sd reason=%s\n' "$action" "$name" "$age" "$reason"
  if [[ "$action" == "DELETE" && $apply -eq 1 ]]; then
    candidate="$root/$name"
    is_descendant "$candidate" "$root" || die "candidate escapes backup root: $candidate"
    [[ -d "$candidate" && ! -L "$candidate" ]] || die "candidate is not a real backup directory: $candidate"
    log "verifying before deletion: $candidate"
    verify_manifest "$candidate"
    rm -rf -- "$candidate"
    log "deleted expired backup: $name"
  fi
done < "$plan"
if ((apply)); then
  log "retention applied"
else
  log "dry run only; rerun with --apply after reviewing this plan"
fi
