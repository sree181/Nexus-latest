"""Push a scanner's SARIF at a MeshAgent run.

Nobody uploads SARIF by hand. It is produced by a CI job, and this is the
step that carries it the last hop, so reachability is asserted by the
scanner your organisation already accredited rather than by MeshAgent's own
AST walk.

    python -m app.upload_scan --run 7f3a --sarif semgrep.sarif

Exits non-zero when the upload fails, so a pipeline notices. It deliberately
does NOT fail on findings: deciding what breaks a build is the tracker's job,
not this script's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def upload(*, api: str, run: str, path: str, token: str | None,
           user: str | None, role: str | None) -> dict:
    with open(path) as fh:
        sarif = json.load(fh)

    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/runs/{run}/scan",
        data=json.dumps(sarif).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # A CI job has no browser, so it authenticates with a token. The local
    # headers are only honoured by a deployment with no provider configured.
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    elif user:
        req.add_header("X-MeshAgent-User", user)
        req.add_header("X-MeshAgent-Role", role or "developer")

    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="the run to attach it to")
    ap.add_argument("--sarif", required=True, help="path to the SARIF file")
    ap.add_argument("--api", default=os.environ.get("MESHAGENT_API",
                                                    "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=os.environ.get("MESHAGENT_TOKEN"))
    ap.add_argument("--user", default=os.environ.get("MESHAGENT_USER"))
    ap.add_argument("--role", default=os.environ.get("MESHAGENT_ROLE"))
    args = ap.parse_args(argv)

    try:
        got = upload(api=args.api, run=args.run, path=args.sarif,
                     token=args.token, user=args.user, role=args.role)
    except FileNotFoundError:
        print(f"no such SARIF file: {args.sarif}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.sarif} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        print(f"MeshAgent refused the scan ({exc.code}): "
              f"{exc.read().decode(errors='replace')[:300]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"could not reach MeshAgent at {args.api}: {exc.reason}",
              file=sys.stderr)
        return 1

    # Say what was covered, not just what was found: coverage is the half a
    # reader needs to know how much the silence is worth.
    print(f"{got['tool']}: {got['reachable']} reachable of {got['results']} "
          f"result(s), over {len(got['modules'])} module(s) "
          f"[{', '.join(got['modules'][:8]) or 'none named'}]")
    return 0


if __name__ == "__main__":       # pragma: no cover - entry point
    raise SystemExit(main())
