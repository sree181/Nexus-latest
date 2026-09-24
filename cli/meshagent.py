#!/usr/bin/env python3
"""MeshAgent developer CLI.

The recording device credential is deliberately kept separate from delegated
human read credentials.  Hooks only use the former; the MCP server selects a
read credential only for read routes.  This command never prints either.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from meshagent_cli import protocol as session_protocol
from meshagent_cli import state

GIVE_UP_AFTER = 600


def _api_url(path: str) -> str:
    return f"{state.endpoint()}/api{path}"


def _request(path: str, body: dict | None = None, *, method: str = "POST",
             token: str | None = None, timeout: float = 10.0,
             headers: dict[str, str] | None = None) -> dict:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    data = None if method == "GET" else json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(_api_url(path), data=data,
                                     headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        decoded = json.loads(raw or "{}")
        return decoded if isinstance(decoded, dict) else {}


def call(path: str, body: dict | None = None, *, method: str = "POST",
         token: str | None = None, timeout: float = 10.0,
         headers: dict[str, str] | None = None) -> dict:
    """One API call, with response bodies reduced to safe operator messages."""
    try:
        return _request(path, body, method=method, token=token, timeout=timeout,
                        headers=headers)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            detail = str(parsed.get("detail") or "") if isinstance(parsed, dict) else ""
        except Exception:
            pass
        raise SystemExit(f"meshagent: {detail or exc.reason} ({exc.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SystemExit(f"meshagent: cannot reach {state.safe_endpoint_for_display()}: {exc}") from None


def _credentials_for_login(token_response: dict) -> dict:
    device = token_response.get("device") or {}
    existing = state.load_credentials()
    body: dict[str, Any] = {
        "token": token_response["token"],
        "api": state.endpoint(),
        "device_id": device.get("id", ""),
        "subject": device.get("subject", ""),
        "name": device.get("name", ""),
        "verified": bool(device.get("verified")),
        "grants": token_response.get("grants", []),
    }
    # Preserve a separately configured read token when rotating a device token.
    if existing.get("read_token"):
        body["read_token"] = existing["read_token"]
    return body


def login(args: argparse.Namespace) -> int:
    """Pair this machine and persist a recording-only device token."""
    if args.api:
        try:
            state.set_endpoint(args.api)
        except state.ConfigurationError as exc:
            print(f"meshagent: invalid API endpoint: {exc}", file=sys.stderr)
            return 2
    label = args.label or os.uname().nodename
    start = call("/devices/pair", {"label": label})
    print(f"\n  Open  {start['verify_url']}")
    print(f"  Enter {start['user_code']}\n")
    print(f"  This will let {label} record as you. It grants "
          f"{', '.join(start.get('grants') or ['recording'])} and nothing else.\n")
    interval = max(1, int(start.get("interval", 2)))
    deadline = time.time() + min(GIVE_UP_AFTER, int(start.get("expires_in", 600)))
    while time.time() < deadline:
        got = call("/devices/token", {"device_code": start["device_code"]})
        if got.get("status") == "granted":
            state.save_credentials(_credentials_for_login(got))
            who = (got.get("device") or {}).get("name") or (got.get("device") or {}).get("subject") or "you"
            print(f"  Signed in. This machine now records as {who}.")
            print(f"  Recording credential stored in {state.credentials_path()} (mode 0600).")
            return 0
        time.sleep(interval)
    print("meshagent: nobody approved that code in time. Try again.", file=sys.stderr)
    return 1


def logout(_: argparse.Namespace) -> int:
    """Best-effort server revocation followed by credential removal."""
    creds = state.load_credentials()
    token = state.device_token()
    if not token:
        print("meshagent: not signed in.")
        return 0
    if device_id := creds.get("device_id"):
        try:
            call(f"/devices/{device_id}", method="DELETE", token=token)
        except SystemExit as exc:
            print(f"  Could not revoke on the server: {exc}", file=sys.stderr)
            print("  Revoke it from the Devices screen when you can.", file=sys.stderr)
    # Retain a separately supplied read credential only when it was deliberately
    # configured.  It cannot be used for hooks, and logout must remove the
    # device writer even during an outage.
    retained = {"read_token": creds["read_token"]} if creds.get("read_token") else {}
    if retained:
        state.save_credentials(retained)
    else:
        try:
            os.unlink(state.credentials_path())
        except OSError:
            pass
    print("  Signed out of recording.")
    return 0


def whoami(_: argparse.Namespace) -> int:
    """Show only non-secret facts about the recording device."""
    creds = state.load_credentials()
    if not state.device_token():
        print("Not signed in. Run `meshagent login`.")
        return 1
    print(f"  Recording as : {creds.get('name') or creds.get('subject') or 'unknown'}")
    print("  Identity     : " + ("verified by the provider" if creds.get("verified") else "asserted, not verified"))
    print(f"  Grants       : {', '.join(creds.get('grants') or ['recording'])}")
    print(f"  API          : {state.safe_endpoint_for_display()}")
    print(f"  Device       : {creds.get('device_id') or 'unknown'}")
    print("  Read token   : " + ("configured (redacted)" if state.read_token() else "not configured"))
    return 0


def config_get(_: argparse.Namespace) -> int:
    print(state.safe_endpoint_for_display())
    return 0


def config_set(args: argparse.Namespace) -> int:
    try:
        endpoint = state.set_endpoint(args.endpoint)
    except state.ConfigurationError as exc:
        print(f"meshagent: invalid API endpoint: {exc}", file=sys.stderr)
        return 2
    print(f"API endpoint saved: {endpoint}")
    return 0


def credentials_set_read(args: argparse.Namespace) -> int:
    try:
        state.set_read_token(args.token)
    except state.ConfigurationError as exc:
        print(f"meshagent: {exc}", file=sys.stderr)
        return 2
    print("Delegated read token saved (redacted). Hooks will not use it.")
    return 0


def credentials_clear_read(_: argparse.Namespace) -> int:
    state.clear_read_token()
    print("Delegated read token removed.")
    return 0


def _health() -> tuple[dict | None, str]:
    try:
        return _request("/health", method="GET", timeout=3.0), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(exc)


def status(args: argparse.Namespace) -> int:
    local = state.diagnostic()
    health, problem = _health()
    if args.json:
        print(json.dumps({"local": local, "health": health, "health_error": problem or None},
                         indent=2, sort_keys=True))
    else:
        print(f"Endpoint: {local['endpoint']} ({local['endpoint_source']})")
        print(f"Recording device: {'configured' if local['credentials']['device'] else 'not configured'}")
        print(f"Delegated read credential: {'configured' if local['credentials']['read'] else 'not configured'}")
        print(f"Queued batches: {local['queued_batches']} across {local['queues']} queue(s)")
        if local["queue_rejected_events"]:
            print(
                "Recording backpressure: "
                f"{local['queue_rejected_events']} observation(s) were not recorded "
                f"across {local['backpressured_sessions']} session(s)"
            )
        if health:
            print(f"API: {health.get('status', 'unknown')} ({health.get('gateway', 'unknown')})")
            mode = health.get("mode") or {}
            print("Persistence: " + ("durable" if health.get("durable") else "not durable")
                  + (f"; {mode.get('note')}" if mode.get("note") else ""))
        else:
            print(f"API: unreachable ({problem})")
    return 0 if health else 1


def doctor(args: argparse.Namespace) -> int:
    details = state.diagnostic()
    problems: list[str] = []
    if details["home_mode"] != "0o700":
        problems.append("MeshAgent home must have mode 0700")
    if details["credentials_present"] and details["credentials_mode"] != "0o600":
        problems.append("credentials file must have mode 0600")
    if details["queue_rejected_events"]:
        problems.append(
            f"local recording queue rejected {details['queue_rejected_events']} "
            "observation(s); run meshagent replay and investigate connectivity"
        )
    health, problem = _health()
    if not health:
        problems.append(f"API health check failed: {problem}")
    elif not health.get("durable"):
        problems.append("API reports non-durable/sample mode; recordings will not persist")
    report = {"diagnostic": details, "health": health, "problems": problems}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("MeshAgent doctor")
        print(f"  endpoint: {details['endpoint']} ({details['endpoint_source']})")
        print(f"  state home: {details['home']} ({details['home_mode']})")
        print(f"  credentials: {'present' if details['credentials_present'] else 'absent'} ({details['credentials_mode']})")
        print(f"  device recording credential: {'present' if details['credentials']['device'] else 'absent'}")
        print(f"  delegated read credential: {'present' if details['credentials']['read'] else 'absent'}")
        print(f"  queued batches: {details['queued_batches']} across {details['queues']} queue(s)")
        print(
            "  recording backpressure: "
            f"{details['queue_rejected_events']} rejected observation(s) across "
            f"{details['backpressured_sessions']} session(s)"
        )
        for issue in problems:
            print(f"  FAIL: {issue}")
        if not problems:
            print("  OK: local configuration and API health check passed")
    return 1 if problems else 0


def _post_replay(batch: dict) -> tuple[dict | None, str]:
    token = state.device_token()
    if not token:
        return None, "recording device credential is not configured"
    if batch.get("protocol") == session_protocol.PROTOCOL:
        response = session_protocol.post(
            batch,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        return (response, "" if response is not None else "delivery failed")
    keyed_events = state.add_event_keys(str(batch.get("agent") or "unknown"),
                                        str(batch.get("session") or ""),
                                        list(batch.get("events") or []))
    payload = dict(batch)
    payload["events"] = keyed_events
    headers = {
        "Idempotency-Key": state.batch_key(payload),
        # Kept separate from Authorization and never printed.  Current API
        # versions ignore it safely; deployments that understand event keys can
        # deduplicate individual events without changing the adapter contract.
        "X-MeshAgent-Event-Keys": ",".join(e["idempotency_key"] for e in keyed_events),
    }
    try:
        return _request("/recorder", payload, token=token, headers=headers), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(exc)


def replay(args: argparse.Namespace) -> int:
    paths = state.queue_files()
    attempted = delivered = retained = 0
    for path in paths:
        with state.lease_path(path) as lease:
            for batch in lease.batches:
                attempted += 1
                _, problem = _post_replay(batch)
                if problem:
                    retained += len(lease.remaining)
                    break
                delivered += 1
                lease.remaining = lease.remaining[1:]
    if args.json:
        print(json.dumps({"queues": len(paths), "attempted": attempted,
                          "delivered": delivered, "retained": retained}, sort_keys=True))
    else:
        print(f"Replay: delivered {delivered}/{attempted} queued batch(es); retained {retained}.")
    return 0 if retained == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meshagent", description="MeshAgent developer integrations")
    subs = parser.add_subparsers(dest="command", required=True)

    p_login = subs.add_parser("login", help="pair this machine for recording")
    p_login.add_argument("--label", default="", help="machine label shown in MeshAgent")
    p_login.add_argument("--api", default="", help="validate and save the API origin before pairing")
    subs.add_parser("logout", help="revoke and remove this recording device")
    subs.add_parser("whoami", help="show non-secret device identity details")

    config_parser = subs.add_parser("config", help="manage durable CLI configuration")
    config_subs = config_parser.add_subparsers(dest="config_command", required=True)
    config_subs.add_parser("get", help="show effective API endpoint")
    p_config_set = config_subs.add_parser("set", help="validate and persist an API origin")
    p_config_set.add_argument("endpoint")

    credential_parser = subs.add_parser("credentials", help="manage local credentials")
    credential_subs = credential_parser.add_subparsers(dest="credential_command", required=True)
    p_set_read = credential_subs.add_parser("set-read-token", help="save delegated human read credential")
    p_set_read.add_argument("token", help="credential value (never printed)")
    credential_subs.add_parser("clear-read-token", help="remove delegated human read credential")

    for name, help_text in (("status", "show local state and API health"),
                            ("doctor", "validate safe local setup and API health"),
                            ("replay", "replay locally queued recorder batches")):
        command = subs.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true", help="machine-readable diagnostic output")

    args = parser.parse_args(argv)
    if args.command == "login":
        return login(args)
    if args.command == "logout":
        return logout(args)
    if args.command == "whoami":
        return whoami(args)
    if args.command == "config":
        return config_get(args) if args.config_command == "get" else config_set(args)
    if args.command == "credentials":
        return credentials_set_read(args) if args.credential_command == "set-read-token" else credentials_clear_read(args)
    if args.command == "status":
        return status(args)
    if args.command == "doctor":
        return doctor(args)
    if args.command == "replay":
        return replay(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
