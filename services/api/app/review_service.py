"""Derived Developer attention and native HyperMesh review evidence queries."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .auth import Principal
from .developer_sessions import Store as SessionStore
from .gateway import Gateway, NotFound
from .workflow_models import AttentionItemOut, AttentionListOut, ReviewGraphOut


_SEVERITY_WEIGHT = {
    "critical": 50, "high": 38, "medium": 24, "low": 12, "unknown": 18,
}
_VERDICT_WEIGHT = {"block": 30, "unknown": 22, "warn": 12, "allow": 0}


def _attention_id(evaluation_id: str) -> str:
    return "att_" + hashlib.sha256(evaluation_id.encode()).hexdigest()[:24]


def _suggested_version(advisories: list[Any]) -> str | None:
    candidates: list[tuple[tuple[int, ...], str]] = []
    for advisory in advisories:
        for version in advisory.fixed_versions:
            match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:[-+].*)?", version.strip())
            if match:
                candidates.append((
                    tuple(int(part) for part in match.group(1).split(".")),
                    version,
                ))
    return max(candidates, default=((), None))[1]


def code_entity_refs(
    gateway: Gateway, run_id: str | None, package: str,
) -> list[dict[str, str]]:
    """Native modules, functions, classes, and APIs linked to this package."""
    if not run_id:
        return []
    try:
        graph = gateway.run_graph(run_id)
    except NotFound:
        return []
    package_ids = {
        node.id for node in graph.nodes
        if node.kind == "package" and (
            node.label.casefold() == package.casefold()
            or node.id.casefold() in {
                f"package:{package}".casefold(), f"pkg:{package}".casefold(),
            }
        )
    }
    linked = {
        edge.source for edge in graph.edges
        if edge.rel == "imports" and edge.target in package_ids
    }
    package_apis = {
        edge.source for edge in graph.edges
        if edge.rel == "api_of" and edge.target in package_ids
    }
    linked |= package_apis
    linked |= {
        edge.source for edge in graph.edges
        if edge.rel == "invokes" and edge.target in package_apis
    }
    return [
        {"id": node.id, "kind": node.kind, "label": node.label}
        for node in sorted(graph.nodes, key=lambda item: item.id)
        if node.id in linked and node.kind in ("class", "module", "function", "api")
    ][:100]


def code_entities(gateway: Gateway, run_id: str | None, package: str) -> list[str]:
    return [item["label"] for item in code_entity_refs(gateway, run_id, package)]


def linked_code_entities(
    sessions: SessionStore, gateway: Gateway, session_id: str,
    who: Principal, run_id: str | None, package: str,
) -> list[str]:
    """Compatibility wrapper backed exclusively by native HyperMesh evidence."""
    del sessions, session_id, who
    return code_entities(gateway, run_id, package)


def attention_items(
    sessions: SessionStore, gateway: Gateway, reviews: Any, who: Principal,
    *, limit: int = 200,
) -> AttentionListOut:
    items: list[AttentionItemOut] = []
    for session in sessions.list(who, limit=200).sessions:
        evaluations = sessions.policy_evaluations(session.id, who, limit=500).evaluations
        for evaluation in evaluations:
            if (
                evaluation.verdict == "allow"
                and not evaluation.advisories
                and not evaluation.unavailable
            ):
                continue
            related = reviews.review_for_evaluation(who.subject, evaluation.id)
            code = code_entities(gateway, session.run_id, evaluation.package)
            severity = evaluation.worst or (
                "unknown" if evaluation.unavailable or evaluation.verdict == "unknown"
                else "low"
            )
            priority = (
                _SEVERITY_WEIGHT.get(severity, 0)
                + _VERDICT_WEIGHT.get(evaluation.verdict, 0)
                + min(15, len(code) * 2)
                + (10 if evaluation.unavailable else 0)
            )
            reasons = [f"{severity} severity", f"{evaluation.verdict} decision"]
            if code:
                reasons.append(f"{len(code)} linked code item(s)")
            if evaluation.unavailable:
                reasons.append("security data unavailable")
            items.append(AttentionItemOut(
                id=_attention_id(evaluation.id), session_id=session.id,
                policy_evaluation_id=evaluation.id, run_id=session.run_id,
                repository_id=session.repository.id,
                repository_name=session.repository.name,
                package=evaluation.package, version=evaluation.version,
                ecosystem=evaluation.ecosystem, verdict=evaluation.verdict,
                worst=evaluation.worst, reasons=evaluation.reasons,
                advisories=evaluation.advisories,
                unavailable=evaluation.unavailable, code_entities=code,
                suggested_version=_suggested_version(evaluation.advisories),
                checked_at_ms=evaluation.evaluated_at_ms,
                review_request_id=related["id"] if related else None,
                review_status=related["state"] if related else None,
                priority=priority, priority_reasons=reasons,
            ))
    ordered = sorted(items, key=lambda item: (-item.priority, -item.checked_at_ms))
    return AttentionListOut(items=ordered[:max(1, min(limit, 500))], total=len(ordered))


def request_graph(
    gateway: Gateway, request: dict[str, Any], perspective: str,
) -> ReviewGraphOut:
    """Return the sealed native review evidence chain, never a UI reconstruction."""
    run_id = request.get("run_id")
    if not run_id or not request.get("evidence_root_ulid"):
        raise NotFound("native review evidence is not projected yet")
    graph = gateway.review_evidence_graph(str(run_id), str(request["id"]))
    note = (
        "Developer scope: this native HyperMesh subgraph contains only your selected "
        "review and the evidence frozen with it."
        if perspective == "developer"
        else "Security-office scope: this native HyperMesh subgraph contains the "
        "submitted review evidence and its append-only decision chain."
    )
    return ReviewGraphOut(
        request_id=request["id"], perspective=perspective,
        evidence_root_ulid=request.get("evidence_root_ulid"),
        evidence_digest=request.get("evidence_digest"),
        graph=graph, note=note,
    )
