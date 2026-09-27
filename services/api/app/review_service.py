"""Derived Developer attention and native HyperMesh review evidence queries."""

from __future__ import annotations

import re
from typing import Any

from .auth import Principal
from .control_plane import ControlPlane
from .developer_session_models import DeveloperSessionOut, PolicyEvaluationOut
from .developer_sessions import Store as SessionStore, attention_id
from .gateway import Gateway, NotFound
from .models import GraphEdge, GraphPayload, Relation
from .workflow_models import (
    AttentionEvidenceAnswerOut,
    AttentionEvidenceOut,
    AttentionEvidenceScopeOut,
    AttentionItemOut,
    AttentionListOut,
    EvidenceStatementOut,
    ReviewGraphOut,
)


_SEVERITY_WEIGHT = {
    "critical": 50, "high": 38, "medium": 24, "low": 12, "unknown": 18,
}
_VERDICT_WEIGHT = {"block": 30, "unknown": 22, "warn": 12, "allow": 0}


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
                id=attention_id(evaluation.id), session_id=session.id,
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


def _subgraph(graph: GraphPayload, relations: list[Relation]) -> GraphPayload:
    """Keep complete native relations and only their member nodes/drawing edges."""
    relation_ids = {relation.id for relation in relations}
    selected_relations = [
        relation for relation in graph.relations if relation.id in relation_ids
    ]
    node_ids = {
        member for relation in selected_relations for member in relation.members
    }
    edges = [
        GraphEdge(
            id=f"{relation.id}:member:{index}",
            source=relation.members[0], target=member, rel=relation.kind,
        )
        for relation in selected_relations
        for index, member in enumerate(relation.members[1:])
    ]
    return GraphPayload(
        nodes=[node for node in graph.nodes if node.id in node_ids],
        edges=edges,
        relations=selected_relations,
    )


def _relations_with_member(
    graph: GraphPayload, member: str, *, kinds: set[str] | None = None,
) -> list[Relation]:
    return [
        relation for relation in graph.relations
        if member in relation.members
        and (kinds is None or relation.kind in kinds)
        and not relation.tombstoned
    ]


def _relation_node_ids(relations: list[Relation]) -> list[str]:
    return list(dict.fromkeys(
        member for relation in relations for member in relation.members
    ))


def _relation_ids(relations: list[Relation]) -> list[str]:
    return [relation.id for relation in relations]


def _why_answer(
    evaluation: PolicyEvaluationOut, graph: GraphPayload,
    *, projection_available: bool,
) -> AttentionEvidenceAnswerOut:
    coordinate = f"{evaluation.package} {evaluation.version}".strip()
    policy_node = f"policy:{evaluation.id}"
    policy_relations = _relations_with_member(
        graph, policy_node, kinds={"policy_evaluation"},
    )
    advisory_relations = [
        relation for advisory in evaluation.advisories
        for relation in _relations_with_member(
            graph, f"cve:{advisory.id}", kinds={"cve"},
        )
    ]
    relations = list({
        relation.id: relation
        for relation in [*policy_relations, *advisory_relations]
    }.values())
    policy_relation_ids = _relation_ids(policy_relations)
    policy_node_ids = _relation_node_ids(policy_relations)
    statements: list[EvidenceStatementOut] = []
    for reason in evaluation.reasons:
        statements.append(EvidenceStatementOut(
            text=reason,
            node_ids=policy_node_ids,
            relation_ids=policy_relation_ids,
        ))
    if evaluation.policy:
        statements.append(EvidenceStatementOut(
            text=f"Recorded policy: {evaluation.policy}",
            node_ids=policy_node_ids,
            relation_ids=policy_relation_ids,
        ))
    for advisory in evaluation.advisories:
        matched = _relations_with_member(
            graph, f"cve:{advisory.id}", kinds={"cve"},
        )
        summary = advisory.summary.strip()
        statements.append(EvidenceStatementOut(
            text=(
                f"{advisory.id} is recorded at {advisory.severity} severity"
                + (f": {summary}" if summary else ".")
            ),
            node_ids=_relation_node_ids(matched),
            relation_ids=_relation_ids(matched),
        ))
    if evaluation.unavailable:
        statements.append(EvidenceStatementOut(
            text=f"Security data was unavailable: {evaluation.unavailable}",
            node_ids=policy_node_ids,
            relation_ids=policy_relation_ids,
        ))

    if evaluation.verdict == "block":
        headline = f"{coordinate} was blocked by the recorded policy evaluation."
    elif evaluation.verdict == "warn":
        headline = f"{coordinate} produced a policy warning."
    elif evaluation.verdict == "unknown":
        headline = f"The policy outcome for {coordinate} is unknown."
    else:
        headline = f"{coordinate} was allowed but still has recorded attention evidence."
    limitations: list[str] = []
    status = "answered"
    if not evaluation.reasons and not evaluation.advisories and not evaluation.policy:
        status = "partial"
        limitations.append("The policy ledger contains no explanatory reason or advisory.")
    if not projection_available or not policy_relations:
        status = "partial"
        limitations.append(
            "Native HyperMesh evidence for this policy evaluation is not projected yet."
        )
    return AttentionEvidenceAnswerOut(
        question="why_blocked", status=status, headline=headline,
        statements=statements, graph=_subgraph(graph, relations),
        limitations=list(dict.fromkeys(limitations)),
    )


def _code_statement(relation: Relation, labels: dict[str, str], package: str) -> str:
    values = [labels.get(member, member.split(":", 1)[-1])
              for member in relation.members]
    code_values = [
        value for member, value in zip(relation.members, values)
        if member.startswith(("module:", "class:", "function:", "api:"))
    ]
    if relation.kind == "module_import" and code_values:
        return f"{code_values[0]} imports {package}."
    if relation.kind == "invocation" and code_values:
        return f"{' invokes '.join(code_values)} through {package}."
    if relation.kind == "class" and code_values:
        return f"{code_values[0]} is recorded with dependency {package}."
    return relation.label


def _affected_answer(
    evaluation: PolicyEvaluationOut, graph: GraphPayload,
    *, projection_available: bool,
) -> AttentionEvidenceAnswerOut:
    package_subjects = {
        f"pkg:{evaluation.package}", f"package:{evaluation.package}",
    }
    relations = [
        relation for relation in graph.relations
        if relation.kind in {"module_import", "invocation", "class"}
        and package_subjects.intersection(relation.members)
        and not relation.tombstoned
    ]
    labels = {node.id: node.label for node in graph.nodes}
    code_node_ids = list(dict.fromkeys(
        member for relation in relations for member in relation.members
        if member.startswith(("module:", "class:", "function:", "api:"))
    ))
    statements = [
        EvidenceStatementOut(
            text=_code_statement(relation, labels, evaluation.package),
            node_ids=relation.members,
            relation_ids=[relation.id],
        )
        for relation in relations
    ]
    limitations: list[str] = []
    if relations:
        headline = (
            f"{len(code_node_ids)} code entit"
            f"{'y is' if len(code_node_ids) == 1 else 'ies are'} natively linked "
            f"to {evaluation.package}."
        )
        status = "answered"
        limitations.append(
            "A recorded dependency link is not proof that the advisory is exploitable in that code path."
        )
    else:
        headline = (
            f"No code relationship to {evaluation.package} was recorded for this evaluation."
        )
        status = "unavailable"
        limitations.append(
            "meshAgent does not infer affected code from unrelated nodes in the run."
        )
    if not projection_available:
        limitations.append(
            "Native HyperMesh evidence for this policy evaluation is not projected yet."
        )
    return AttentionEvidenceAnswerOut(
        question="affected_code", status=status, headline=headline,
        statements=statements, graph=_subgraph(graph, relations),
        limitations=list(dict.fromkeys(limitations)),
    )


def _outcome_answer(
    evaluation: PolicyEvaluationOut, graph: GraphPayload,
    review: dict[str, Any] | None, *, projection_available: bool,
) -> AttentionEvidenceAnswerOut:
    fixed_versions = list(dict.fromkeys(
        version for advisory in evaluation.advisories
        for version in advisory.fixed_versions if version.strip()
    ))
    cve_relations = [
        relation for advisory in evaluation.advisories
        for relation in _relations_with_member(
            graph, f"cve:{advisory.id}", kinds={"cve"},
        )
    ]
    policy_relations = _relations_with_member(
        graph, f"policy:{evaluation.id}", kinds={"policy_evaluation"},
    )
    review_relations = [
        relation for relation in graph.relations
        if relation.kind == "review_event"
        and review is not None
        and f"review:{review['id']}" in relation.members
        and not relation.tombstoned
    ]
    relations = list({
        relation.id: relation
        for relation in [*policy_relations, *cve_relations, *review_relations]
    }.values())
    statements: list[EvidenceStatementOut] = []
    for advisory in evaluation.advisories:
        candidates = list(dict.fromkeys(
            version for version in advisory.fixed_versions if version.strip()
        ))
        if not candidates:
            continue
        matched = _relations_with_member(
            graph, f"cve:{advisory.id}", kinds={"cve"},
        )
        statements.append(EvidenceStatementOut(
            text=(
                f"{advisory.id} reports fixed-version candidate"
                f"{'s' if len(candidates) != 1 else ''}: {', '.join(candidates)}."
            ),
            node_ids=_relation_node_ids(matched),
            relation_ids=_relation_ids(matched),
        ))
    if review is not None:
        statements.append(EvidenceStatementOut(
            text=(
                f"Security review {review['id']} is "
                f"{str(review['state']).replace('_', ' ')}."
            ),
            node_ids=_relation_node_ids(review_relations),
            relation_ids=_relation_ids(review_relations),
        ))
        if review.get("recommended_version"):
            statements.append(EvidenceStatementOut(
                text=(
                    "Security recorded recommended version "
                    f"{review['recommended_version']}."
                ),
                node_ids=_relation_node_ids(review_relations),
                relation_ids=_relation_ids(review_relations),
            ))

    if review is not None and review.get("state") == "verified":
        headline = "A later package evaluation verified the recorded remediation."
        status = "answered"
    elif review is not None and review.get("recommended_version"):
        headline = (
            f"Security recorded {review['recommended_version']} as the next version to evaluate."
        )
        status = "partial"
    elif fixed_versions:
        headline = (
            f"Published advisory data identifies {', '.join(fixed_versions)} "
            "as fixed-version candidate evidence."
        )
        status = "partial"
    elif review is not None:
        headline = (
            f"Security review {review['id']} is the recorded path for changing the outcome."
        )
        status = "partial"
    else:
        headline = "No outcome-changing evidence is recorded yet."
        status = "unavailable"

    limitations = [
        "A fixed-version candidate is not a guaranteed allow decision; compatibility and policy must be evaluated again."
    ]
    if not projection_available:
        limitations.append(
            "Native HyperMesh evidence for this policy evaluation is not projected yet."
        )
    return AttentionEvidenceAnswerOut(
        question="outcome_change", status=status, headline=headline,
        statements=statements, graph=_subgraph(graph, relations),
        limitations=list(dict.fromkeys(limitations)),
    )


def attention_evidence(
    sessions: SessionStore, gateway: Gateway, reviews: ControlPlane,
    who: Principal, attention_id: str,
) -> AttentionEvidenceOut:
    """Derive three traceable answers for one owner-scoped Attention item."""
    session, evaluation = sessions.attention_evaluation(attention_id, who)
    if (
        evaluation.verdict == "allow"
        and not evaluation.advisories
        and not evaluation.unavailable
    ):
        raise NotFound("unknown attention item")
    if not session.run_id:
        raise NotFound("native policy-evaluation evidence is not projected yet")

    projection_available = True
    try:
        graph = gateway.policy_evaluation_evidence_graph(
            session.run_id, evaluation.id,
        )
    except NotFound:
        graph = GraphPayload()
        projection_available = False
    review = reviews.review_for_evaluation(who.subject, evaluation.id)
    scope = AttentionEvidenceScopeOut(
        attention_id=attention_id,
        session_id=session.id,
        policy_evaluation_id=evaluation.id,
        run_id=session.run_id,
        repository_id=session.repository.id,
        repository_name=session.repository.name,
        package=evaluation.package,
        version=evaluation.version,
        ecosystem=evaluation.ecosystem,
        checked_at_ms=evaluation.evaluated_at_ms,
        review_request_id=review["id"] if review else None,
        review_status=review["state"] if review else None,
    )
    return AttentionEvidenceOut(
        scope=scope,
        why_blocked=_why_answer(
            evaluation, graph, projection_available=projection_available,
        ),
        affected_code=_affected_answer(
            evaluation, graph, projection_available=projection_available,
        ),
        outcome_change=_outcome_answer(
            evaluation, graph, review,
            projection_available=projection_available,
        ),
    )


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
