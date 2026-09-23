#!/usr/bin/env python3
"""`meshagent` -- the command a developer runs once so their hooks have a name.

The recorder's value depends on it running unattended, which means the thing
doing the recording is a shell command with no browser. It cannot do the OIDC
redirect the web UI does. Until this existed it fell back to asserting a name
in a header, which meant the coverage report named developers against gaps on
the strength of a string anyone could type into curl.

So: the device authorization grant, in shape. This command asks the API to
start a pairing, prints a short code, and waits. The developer opens
MeshAgent in a browser they are already signed into and approves that code.
Only then does a token exist, and it records as the human who approved it.

The token is scoped to recording and nothing else, because it is about to be
written to a file on a laptop and left there. Worst case is fabricated
memory, which is visible in the audit log and recoverable; the alternative --
a general-purpose credential in the same file -- would be read access to
every run in the fleet.

Standard library only, like the hooks that use what it writes: a developer
should not need a virtualenv to log in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HOME_ENV = "MESHAGENT_HOOK_HOME"
API_ENV = "MESHAGENT_API"
DEFAULT_API = "http://localhost:8000"

CREDENTIALS = "credentials.json"

# The developer is reading a code off one screen and typing it into another.
# Long enough to be worth showing progress, short enough that an abandoned
# login does not sit there being phishable.
GIVE_UP_AFTER = 600


def home() -> str:
    """Where the adapter keeps its state. The same directory the hooks read,
    deliberately: a login the hook cannot find has not logged anything in."""
    base = os.environ.get(HOME_ENV) or os.path.expanduser("~/.meshagent")
    os.makedirs(base, mode=0o700, exist_ok=True)
    return base


def credentials_path() -> str:
    return os.path.join(home(), CREDENTIALS)


def api() -> str:
    return os.environ.get(API_ENV, DEFAULT_API).rstrip("/")


def load_credentials() -> dict:
    try:
        with open(credentials_path()) as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(body: dict) -> None:
    """Write the token 0600, and create it 0600 rather than fixing the mode
    afterwards -- between the two there is a moment where it is not."""
    path = credentials_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(body, fh, indent=2)


def call(path: str, body: dict | None = None, *, method: str = "POST",
         token: str | None = None, timeout: float = 10.0) -> dict:
    """One API call, with errors turned into something a person can read."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{api()}/api{path}",
        data=json.dumps(body or {}).encode() if method != "GET" else None,
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("detail", "")
        except Exception:
            pass
        raise SystemExit(f"meshagent: {detail or exc.reason} ({exc.code})")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"meshagent: cannot reach {api()}: {exc}")


# -- commands ------------------------------------------------------------------

def login(args: argparse.Namespace) -> int:
    """Pair this machine with the human at the browser."""
    label = args.label or f"{os.uname().nodename}"
    start = call("/devices/pair", {"label": label})

    print(f"\n  Open  {start['verify_url']}")
    print(f"  Enter {start['user_code']}\n")
    print(f"  This will let {label} record as you. It grants "
          f"{', '.join(start.get('grants') or ['recording'])} and nothing "
          f"else -- it cannot read runs or delete anything.\n")

    interval = max(1, int(start.get("interval", 2)))
    deadline = time.time() + min(GIVE_UP_AFTER, int(start.get("expires_in", 600)))
    while time.time() < deadline:
        got = call("/devices/token", {"device_code": start["device_code"]})
        if got.get("status") == "granted":
            device = got.get("device") or {}
            save_credentials({
                "token": got["token"],
                "api": api(),
                "device_id": device.get("id", ""),
                "subject": device.get("subject", ""),
                "name": device.get("name", ""),
                # Carried so `whoami` can say it without another call, and so
                # the developer is told plainly when this deployment has no
                # identity provider and their name was only asserted.
                "verified": bool(device.get("verified")),
                "grants": got.get("grants", []),
            })
            who = device.get("name") or device.get("subject") or "you"
            print(f"  Signed in. This machine now records as {who}.")
            if not device.get("verified"):
                print("  Note: this deployment has no identity provider, so "
                      "that name was asserted, not proven.")
            print(f"  Token stored in {credentials_path()} (mode 0600).")
            return 0
        time.sleep(interval)

    print("meshagent: nobody approved that code in time. Try again.",
          file=sys.stderr)
    return 1


def logout(_: argparse.Namespace) -> int:
    """Revoke the token and remove it. Revoking first, because a token
deleted locally but still live on the server is not revoked."""
    creds = load_credentials()
    if not creds.get("token"):
        print("meshagent: not signed in.")
        return 0
    if device_id := creds.get("device_id"):
        # Best effort: the point of the command is that the local copy is
        # gone, and a server that cannot be reached must not leave it behind.
        try:
            call(f"/devices/{device_id}", method="DELETE",
                 token=creds["token"])
        except SystemExit as exc:
            print(f"  Could not revoke on the server: {exc}", file=sys.stderr)
            print("  Revoke it from the Devices screen when you can.",
                  file=sys.stderr)
    try:
        os.unlink(credentials_path())
    except OSError:
        pass
    print("  Signed out.")
    return 0


def whoami(_: argparse.Namespace) -> int:
    """What this machine records as, and whether anyone verified it."""
    creds = load_credentials()
    if not creds.get("token"):
        print("Not signed in. Run `meshagent login`.")
        return 1
    print(f"  Recording as : {creds.get('name') or creds.get('subject')}")
    print(f"  Identity     : "
          f"{'verified by the provider' if creds.get('verified') else 'asserted, not verified'}")
    print(f"  Grants       : {', '.join(creds.get('grants') or ['recording'])}")
    print(f"  API          : {creds.get('api')}")
    print(f"  Device       : {creds.get('device_id')}")
    return 0


COMMANDS = {"login": login, "logout": logout, "whoami": whoami}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meshagent",
        description="Register this machine so its agent hooks record as you.")
    subs = parser.add_subparsers(dest="command", required=True)

    p_login = subs.add_parser("login", help="pair this machine")
    p_login.add_argument("--label", default="",
                         help="what to call this machine in the UI")
    subs.add_parser("logout", help="revoke this machine's token")
    subs.add_parser("whoami", help="show what this machine records as")

    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
