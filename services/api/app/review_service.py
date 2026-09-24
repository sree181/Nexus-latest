"""Derived Developer attention and role-scoped review evidence graphs."""

from __future__ import annotations

import hashlib
import ast
import re
import re
from typing import Any

from .auth import Principal
from .developer_sessions import Store as SessionStore
from .gateway import Gateway, NotFound
from .models import GraphEdge, GraphNode, GraphPayload, Relation
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


def code_entities(gateway: Gateway, run_id: str | None, package: str) -> list[str]:
    """Code nodes the recorded graph directly says import this package."""
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
            or node.id.casefold() == f"package:{package}".casefold()
        )
    }
    linked = {
        edge.source for edge in graph.edges
        if edge.rel == "imports" and edge.target in package_ids
    }
    labels = {node.id: node.label for node in graph.nodes}
    return sorted({labels.get(node_id, node_id) for node_id in linked})[:100]


def activity_code_entities(
    sessions: SessionStore, session_id: str, who: Principal, package: str,
) -> list[str]:
    """Recorded files whose source actually imports the package."""
    wanted = package.casefold().replace("-", "_")
    found: set[str] = set()
    events = sessions.events(session_id, who, limit=500).events
    for event in events:
        if event.type != "file.changed":
            continue
        path = str(event.payload.get("path") or "")
        code = event.payload.get("code")
        if not path or not isinstance(code, str):
            continue
        imports: set[str] = set()
        try:
            tree = ast.parse(code)
            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    imports.update(alias.name.split(".", 1)[0].casefold() for alias in item.names)
                elif isinstance(item, ast.ImportFrom) and item.module:
                    imports.add(item.module.split(".", 1)[0].casefold())
        except SyntaxError:
            for match in re.finditer(
                r"(?:from\s+|require\s*\(\s*|import\s*\(\s*)['\"]([^'\"]+)['\"]",
                code,
            ):
                imports.add(match.group(1).split("/", 1)[0].casefold())
        if wanted in {value.replace("-", "_") for value in imports}:
            found.add(path)
    return sorted(found)[:100]


def linked_code_entities(
    sessions: SessionStore, gateway: Gateway, session_id: str,
    who: Principal, run_id: str | None, package: str,
) -> list[str]:
    """Code proven by either projected graph edges or recorded source imports."""
    return sorted(set(
        code_entities(gateway, run_id, package)
        + activity_code_entities(sessions, session_id, who, package)
    ))[:100]


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
            code = linked_code_entities(
                sessions, gateway, session.id, who,
                session.run_id, evaluation.package,
            )
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


def request_graph(request: dict[str, Any], perspective: str) -> ReviewGraphOut:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    relations: list[Relation] = []

    def node(node_id: str, kind: str, label: str, **extra: Any) -> str:
        if not any(existing.id == node_id for existing in nodes):
            nodes.append(GraphNode(id=node_id, kind=kind, label=label, **extra))
        return node_id

    def edge(source: str, target: str, rel: str) -> None:
        edges.append(GraphEdge(
            id=f"rev-e{len(edges) + 1}", source=source, target=target, rel=rel,
        ))

    developer = node(
        f"agent:{request['owner_subject']}", "agent", request["owner_name"],
        owners=[request["owner_subject"]],
    )
    repository = node(
        f"source:{request['repository_id']}", "source", request["repository_name"],
        owners=[request["owner_subject"]],
    )
    session = node(
        f"session:{request['session_id']}", "other", "Coding session",
        owners=[request["owner_subject"]],
    )
    package = node(
        f"package:{request['ecosystem']}:{request['package']}", "package",
        request["package"], owners=[request["owner_subject"]],
    )
    version_label = request["version"] or "unpinned"
    version = node(
        f"version:{request['ecosystem']}:{request['package']}@{version_label}",
        "version", version_label, owners=[request["owner_subject"]],
    )
    review = node(
        f"decision:{request['id']}", "decision",
        request["state"].replace("_", " ").title(),
        owners=[request["owner_subject"]],
    )
    edge(developer, repository, "contributed_to")
    edge(session, repository, "observed_in")
    edge(session, version, "checked")
    edge(package, version, "has_version")
    edge(review, version, "reviews")

    advisory_ids: list[str] = []
    for advisory in request["advisories"]:
        advisory_id = node(
            f"cve:{advisory['id']}", "cve", advisory["id"],
            severity=advisory.get("severity") or "unknown",
            exploitable=(advisory.get("severity") in ("critical", "high")),
        )
        advisory_ids.append(advisory_id)
        edge(advisory_id, version, "affects")

    code_ids: list[str] = []
    for index, label in enumerate(request["code_entities"][:50]):
        code_id = node(
            f"class:review:{index}:{hashlib.sha256(label.encode()).hexdigest()[:10]}",
            "class", label, owners=[request["owner_subject"]],
        )
        code_ids.append(code_id)
        edge(code_id, package, "imports")

    relation_members = [session, version, review, *advisory_ids, *code_ids]
    relations.append(Relation(
        id=f"review:{request['id']}", kind="review",
        members=relation_members,
        label=f"{request['package']} review evidence",
    ))
    relations.append(Relation(
        id=f"contribution:{request['id']}", kind="contribution",
        members=[developer, repository, session],
        label="Contributor and repository context",
    ))
    if request.get("analyst_subject"):
        analyst = node(
            f"agent:{request['analyst_subject']}", "agent",
            request.get("analyst_name") or "Reviewer",
        )
        edge(analyst, review, "decided")
        relations.append(Relation(
            id=f"decision:{request['id']}", kind="decision",
            members=[analyst, review, version], label="Review decision",
        ))

    note = (
        "Developer scope: this graph contains only your selected review, its "
        "repository, directly linked code entities, package, advisories, and reviewer decision."
        if perspective == "developer"
        else "Security-office scope: this graph contains the submitted evidence snapshot and named participants required to decide this review; unrelated developer activity is excluded."
    )
    return ReviewGraphOut(
        request_id=request["id"], perspective=perspective,
        graph=GraphPayload(nodes=nodes, edges=edges, relations=relations), note=note,
    )
