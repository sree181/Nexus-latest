"""Shared connected-agent session protocol used by Cursor and Claude Code hooks."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import state as local_state

PROTOCOL = "meshagent.session.v1"


def _ms() -> int:
    return int(time.time() * 1000)


def _repo(repository: str) -> dict[str, Any]:
    return {
        "id": local_state.repository_id(repository),
        "name": os.path.basename(os.path.realpath(repository)) or "repository",
    }


def _normalise(event: dict, *, sequence: int) -> dict:
    event_id = str(event.get("event_id") or local_state.new_event_id())
    common = {
        "event_id": event_id,
        "source_event_id": str(event.get("source_event_id") or event_id),
        "sequence": sequence,
        "occurred_at_ms": int(event.get("occurred_at_ms") or _ms()),
    }
    kind = event.get("type")
    if kind == "code":
        return {
            **common,
            "type": "file.changed",
            "payload": {
                "path": event["module"],
                "operation": event.get("operation", "update"),
                "code": event.get("code"),
                "because": event.get("because"),
            },
        }
    if kind == "package":
        return {
            **common,
            "type": "package.installed",
            "payload": {
                "package": event["package"],
                "version": event.get("version", ""),
                "license": event.get("license", "unknown"),
                "ecosystem": event.get("ecosystem"),
                "command": event.get("command"),
            },
        }
    if kind == "policy":
        return {
            **common,
            "type": "policy.evaluated",
            "payload": {
                "package": event["package"],
                "version": event.get("version", ""),
                "verdict": event["verdict"],
                "reasons": event.get("reasons") or [],
                "policy": event.get("policy") or "",
                "worst": event.get("worst"),
                "unavailable": event.get("unavailable"),
                "advisories": event.get("advisories") or [],
            },
        }
    if kind == "tool":
        failed = bool(event.get("failed"))
        return {
            **common,
            "type": "tool.failed" if failed else "tool.completed",
            "payload": {
                "tool_name": event["name"],
                "detail": event.get("detail", ""),
                "exit_code": event.get("exit_code"),
            },
        }
    if kind == "decision":
        return {
            **common,
            "type": "decision.recorded",
            "payload": {
                "decision_id": event["id"],
                "statement": event["statement"],
            },
        }
    if kind == "prompt":
        return {
            **common,
            "type": "prompt.submitted",
            "payload": {
                "prompt": event["prompt"],
                "turn_id": event.get("turn_id"),
            },
        }
    if kind == "response":
        return {
            **common,
            "type": "response.completed",
            "payload": {
                "turn_id": event.get("turn_id"),
                "summary": event.get("summary", ""),
                "stop_reason": event.get("stop_reason"),
            },
        }
    if kind == "session" and event.get("ends"):
        return {
            **common,
            "type": "session.ended",
            "payload": {"reason": event.get("reason", "normal")},
        }
    raise ValueError(f"unsupported adapter event type {kind!r}")


def _queue_item(
    *, agent: str, native_session: str, events: list[dict], repository: str,
    adapter_version: str,
) -> dict:
    opening = next(
        (event for event in events if event.get("type") == "session" and not event.get("ends")),
        None,
    )
    reserved: dict[str, Any] = {}

    def update(state: dict) -> dict:
        state.setdefault("protocol_version", 2)
        state.setdefault("developer_session_id", local_state.new_session_id())
        state.setdefault("next_sequence", 2)
        reserved["session_id"] = state["developer_session_id"]
        if opening is not None:
            body = state.get("opening_request")
            if not isinstance(body, dict):
                source_event_id = str(
                    opening.get("source_event_id") or opening.get("event_id")
                    or local_state.new_event_id()
                )
                body = {
                    "id": state["developer_session_id"],
                    "source_session_id": native_session,
                    "source_event_id": source_event_id,
                    "adapter": agent,
                    "adapter_version": adapter_version,
                    "repository": _repo(repository),
                    "task": opening["task"],
                    "started_at_ms": int(opening.get("occurred_at_ms") or _ms()),
                    "sequence": 1,
                }
                state["opening_request"] = body
            reserved["opening_request"] = dict(body)
        else:
            # Read the next sequence here, but do not advance it until the
            # complete normalized item is durably present in the queue. If the
            # process dies between those writes, queue replay remains the
            # source of truth and the server acknowledgement repairs state.
            reserved["first_sequence"] = int(state["next_sequence"])
        return state

    state = local_state.update_session(
        native_session, update, repository=repository, editor=agent,
    )
    if opening is not None:
        body = reserved["opening_request"]
        return {
            "protocol": PROTOCOL,
            "kind": "start",
            "session": native_session,
            "session_id": reserved["session_id"],
            "path": "/api/v1/developer/sessions",
            "body": body,
        }

    first = int(reserved["first_sequence"])
    body = {
        "events": [
            _normalise(event, sequence=first + index)
            for index, event in enumerate(events)
        ]
    }
    return {
        "protocol": PROTOCOL,
        "kind": "events",
        "session": native_session,
        "session_id": reserved["session_id"],
        "path": f"/api/v1/developer/sessions/{reserved['session_id']}/events",
        "body": body,
    }


def _acknowledged(item: dict, response: dict | None) -> bool:
    if not isinstance(response, dict):
        return False
    if item.get("protocol") != PROTOCOL:
        return True
    if item.get("kind") == "start":
        try:
            acknowledged = int(response.get("last_acked_sequence") or 0)
        except (TypeError, ValueError):
            return False
        return response.get("id") == item.get("session_id") and acknowledged >= 1
    if item.get("kind") == "events":
        events = (item.get("body") or {}).get("events") or []
        if not events:
            return False
        expected = int(events[-1]["sequence"])
        try:
            acknowledged = int(response.get("acknowledged_through") or 0)
        except (TypeError, ValueError):
            return False
        return acknowledged >= expected
    return False


def post(item: dict, *, headers: dict[str, str], timeout: float) -> dict | None:
    api = local_state.endpoint()
    path = str(item.get("path") or "/api/recorder")
    body = item.get("body") if item.get("protocol") == PROTOCOL else item
    request_headers = dict(headers)
    request_headers["Content-Type"] = "application/json"
    request_headers["Idempotency-Key"] = local_state.batch_key(item)
    req = urllib.request.Request(
        f"{api}{path}", data=json.dumps(body).encode(),
        headers=request_headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            decoded = json.loads(response.read().decode())
            return decoded if _acknowledged(item, decoded) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def send(
    *, agent: str, native_session: str, events: list[dict], repository: str,
    headers: dict[str, str], timeout: float, adapter_version: str = "1",
    post_fn: Callable[[dict], dict | None] | None = None,
) -> dict | None:
    """Reserve sequence numbers once, replay oldest records, then send new work."""
    if not events:
        return None
    queue_path = local_state.queue_path(
        native_session, repository=repository, editor=agent,
    )

    def deliver() -> tuple[bool, dict | None]:
        last: dict | None = None
        with local_state.lease_queue(
            repository=repository, editor=agent, session_id=native_session,
        ) as lease:
            for queued in lease.batches:
                response = (
                    post_fn(queued) if post_fn is not None
                    else post(queued, headers=headers, timeout=timeout)
                )
                if not _acknowledged(queued, response):
                    return False, last
                last = response
                lease.remaining = lease.remaining[1:]
        return True, last

    def acknowledge(response: dict) -> None:
        def update(state: dict) -> dict:
            if value := response.get("run_id"):
                state["run_id"] = value
            state["last_ok"] = int(time.time())
            acknowledged = int(
                response.get("acknowledged_through")
                or response.get("last_acked_sequence")
                or state.get("last_server_sequence", 0)
            )
            state["last_server_sequence"] = acknowledged
            state["next_sequence"] = max(
                int(state.get("next_sequence", 2)), acknowledged + 1,
            )
            if acknowledged >= 1 and isinstance(state.get("opening_request"), dict):
                state["opened"] = True
                state["opening_acknowledged"] = True
                state["task"] = state["opening_request"].get("task", "")
            return state
        local_state.update_session(
            native_session, update, repository=repository, editor=agent,
        )

    with local_state.locked(queue_path + ".sender"):
        available, previous = deliver()
        if previous is not None:
            acknowledge(previous)
        opening = any(
            event.get("type") == "session" and not event.get("ends")
            for event in events
        )
        if available and not opening:
            state = local_state.read_session(
                native_session, repository=repository, editor=agent,
            )
            saved = state.get("opening_request")
            if not state.get("opening_acknowledged") and isinstance(saved, dict):
                opener = {
                    "protocol": PROTOCOL,
                    "kind": "start",
                    "session": native_session,
                    "session_id": state["developer_session_id"],
                    "path": "/api/v1/developer/sessions",
                    "body": saved,
                }
                if not local_state.enqueue(
                    opener, repository=repository, editor=agent,
                    session_id=native_session,
                ):
                    return None
        item = _queue_item(
            agent=agent, native_session=native_session, events=events,
            repository=repository, adapter_version=adapter_version,
        )
        retained = local_state.enqueue(
            item, repository=repository, editor=agent, session_id=native_session,
        )
        if not retained:
            count = len(item["body"].get("events") or [item["body"]])

            def release(state: dict) -> dict:
                state["queue_rejected_events"] = int(
                    state.get("queue_rejected_events", 0)
                ) + count
                state["queue_error_at"] = int(time.time())
                state["queue_error"] = (
                    "local recording queue is full; accepted events were preserved "
                    "and this observation was not recorded"
                )
                return state

            local_state.update_session(
                native_session, release, repository=repository, editor=agent,
            )
            return None
        if item["kind"] == "events":
            first = int(item["body"]["events"][0]["sequence"])
            count = len(item["body"]["events"])

            def reserve(state: dict) -> dict:
                state["next_sequence"] = max(
                    int(state.get("next_sequence", first)), first + count,
                )
                return state

            local_state.update_session(
                native_session, reserve, repository=repository, editor=agent,
            )
        else:
            local_state.update_session(
                native_session,
                lambda state: {**state, "opening_enqueued": True},
                repository=repository,
                editor=agent,
            )
        if not available:
            return None
        delivered, current = deliver()
        if not delivered or current is None:
            return None
        acknowledge(current)
        return current
