"""Projection from normalized session activity into governed HyperMesh memory."""

from __future__ import annotations

from .auth import Principal
from .developer_session_models import ActivityEventOut, DeveloperSessionOut
from .developer_sessions import Store
from .gateway import Gateway
from .models import RecorderBatch, RecorderReceipt


def _legacy_event(event: ActivityEventOut, session: DeveloperSessionOut) -> dict | None:
    payload = event.payload
    common = {"event_id": event.event_id, "at": event.occurred_at_ms // 1000}
    if event.type == "session.started":
        return {
            "type": "session", "agent": session.adapter, "task": session.task,
            **common,
        }
    if event.type == "session.ended":
        return {
            "type": "session", "agent": session.adapter, "task": session.task,
            "ends": True, **common,
        }
    if event.type == "decision.recorded":
        return {
            "type": "decision", "id": payload["decision_id"],
            "statement": payload["statement"], **common,
        }
    if event.type == "file.changed" and payload.get("operation") != "delete":
        return {
            "type": "code", "module": payload["path"], "code": payload["code"],
            "because": payload.get("because"), **common,
        }
    if event.type == "file.changed":
        return {
            "type": "tool", "name": "FileDelete", "detail": payload["path"],
            **common,
        }
    if event.type == "package.installed":
        return {
            "type": "package", "package": payload["package"],
            "version": payload.get("version", ""),
            "license": payload.get("license", "unknown"), **common,
        }
    if event.type in ("tool.completed", "tool.failed"):
        detail = payload.get("detail", "")
        if event.type == "tool.failed":
            detail = f"failed: {detail}".strip()
        return {
            "type": "tool", "name": payload["tool_name"], "detail": detail,
            **common,
        }
    # Prompt turns, tool starts, package requests, policy decisions and response
    # summaries remain first-class operational activity in SQLite. They do not
    # claim a new governed fact in HyperMesh merely because they occurred.
    return None


def project_pending(
    store: Store, gateway: Gateway, session: DeveloperSessionOut,
    who: Principal,
) -> tuple[int, list[str], str | None]:
    """Project pending events in order and return counts plus the run id.

    The activity ledger is authoritative for delivery and replay. The gateway is
    authoritative for governed evidence. A failed projection remains retryable;
    an adapter replay or the startup recovery path can attempt it again.
    """
    projected = 0
    refused: list[str] = []
    run_id = session.run_id
    with store.projecting():
        while True:
            pending = store.projectable(session.id)
            if not pending:
                break
            for event in pending:
                store.mark_projecting(event.event_id)
                legacy = _legacy_event(event, session)
                if legacy is None:
                    store.mark_projected(event.event_id, run_id=run_id or "")
                    projected += 1
                    continue
                try:
                    receipt: RecorderReceipt = gateway.record_events(
                        RecorderBatch(
                            agent=session.adapter,
                            # The ledger ID is globally unique for this
                            # deployment. Native editor IDs are only unique in
                            # their adapter/repository namespace and therefore
                            # cannot safely identify a governed engine run.
                            session=session.id,
                            events=[legacy],
                        ),
                        owner=who.subject,
                        owner_name=who.name,
                        attributed=who.verified,
                    )
                except Exception as exc:
                    store.mark_projection_failed(event.event_id, str(exc))
                    return projected, refused, run_id
                run_id = receipt.run_id
                store.bind_run(session.id, run_id)
                refusal = "; ".join(receipt.refused) if receipt.refused else None
                store.mark_projected(event.event_id, run_id=run_id, refused=refusal)
                projected += 1
                refused.extend(receipt.refused)
    return projected, refused, run_id
