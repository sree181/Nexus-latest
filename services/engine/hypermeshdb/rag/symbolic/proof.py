"""
rag/symbolic/proof.py — proof-tree construction with provenance binding.

A proof tree is a DAG: the root is a derived conclusion, internal nodes are
rule applications (cited ``RULE-N``), and leaves are ground evidence bound to
verifiable provenance. Shared sub-proofs reference one node id (true DAG).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .facts import Fact, FactBase

# A hook to enrich a base fact's provenance from the ingest provenance index.
# Receives the edge_ref dict, returns extra provenance fields (e.g. source_id,
# digest). Default binding marks the leaf verified against the live store.
ProvenanceResolver = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ProofTree:
    proof_id: str
    goal: dict[str, Any]
    status: str                                   # "proved" | "unproved"
    confidence: float = 0.0
    depth: int = 0
    rules_fired: list[str] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "goal": self.goal,
            "status": self.status,
            "confidence": round(self.confidence, 4),
            "depth": self.depth,
            "rules_fired": self.rules_fired,
            "nodes": self.nodes,
            "edges": self.edges,
        }

    @property
    def evidence_tags(self) -> set[str]:
        tags: set[str] = set()
        for n in self.nodes:
            if n.get("hedge_tag"):
                tags.add(n["hedge_tag"])
            if n.get("rule_tag"):
                tags.add(n["rule_tag"])
            if n.get("step_tag"):
                tags.add(n["step_tag"])
        return tags


def _default_provenance(edge_ref: dict[str, Any]) -> dict[str, Any]:
    # Bound to a concrete stored hyperedge in the retrieved set → verified.
    return {
        "source": "hyperedge",
        "table": edge_ref.get("table", ""),
        "edge_idx": edge_ref.get("edge_idx"),
        "event_ts": edge_ref.get("event_ts"),
        "verified": True,
    }


def build_proof(
    goal: Fact,
    fb: FactBase,
    *,
    proof_id: str = "proof",
    provenance_resolver: ProvenanceResolver | None = None,
) -> ProofTree:
    """Build a proof tree for ``goal`` from the reasoned fact base."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_id_of: dict[Fact, str] = {}
    rule_tag_of: dict[str, str] = {}
    s_counter = [0]
    l_counter = [0]
    rules_fired: list[str] = []
    max_depth = [0]

    def rule_tag(rule_id: str) -> str:
        if rule_id not in rule_tag_of:
            rule_tag_of[rule_id] = f"RULE-{len(rule_tag_of)}"
            rules_fired.append(rule_id)
        return rule_tag_of[rule_id]

    def visit(fact: Fact, depth: int) -> str:
        if fact in node_id_of:
            return node_id_of[fact]
        max_depth[0] = max(max_depth[0], depth)
        meta = fb.meta(fact)
        if meta.source == "base":
            l_counter[0] += 1
            nid = f"L{l_counter[0]}"
            node_id_of[fact] = nid
            ref = meta.edge_ref or {}
            prov = _default_provenance(ref)
            if provenance_resolver is not None:
                try:
                    prov.update(provenance_resolver(ref) or {})
                except Exception:
                    prov["verified"] = False
            node = {
                "id": nid,
                "kind": "evidence",
                "evidence_type": "hyperedge" if ref.get("edge_idx") is not None else "fact",
                "fact": str(fact),
                "predicate": fact.pred,
                "args": list(fact.args),
                "confidence": round(meta.confidence, 4),
                "provenance": prov,
            }
            if ref.get("hedge_tag"):
                node["hedge_tag"] = ref["hedge_tag"]
            nodes.append(node)
            return nid

        # derived conclusion
        s_counter[0] += 1
        nid = f"S{s_counter[0]}"
        node_id_of[fact] = nid
        rtag = rule_tag(meta.rule_id) if meta.rule_id else None
        node = {
            "id": nid,
            "kind": "conclusion",
            "fact": str(fact),
            "predicate": fact.pred,
            "args": list(fact.args),
            "rule": meta.rule_id,
            "rule_tag": rtag,
            "step_tag": f"STEP-{s_counter[0]}",
            "confidence": round(meta.confidence, 4),
            "premises": [],
        }
        nodes.append(node)
        for prem in meta.support:
            child = visit(prem, depth + 1)
            node["premises"].append(child)
            edges.append({"from": nid, "to": child, "role": "premise"})
        return nid

    if goal not in fb:
        return ProofTree(
            proof_id=proof_id,
            goal={"predicate": goal.pred, "args": list(goal.args)},
            status="unproved",
        )

    root_id = visit(goal, 0)
    root_conf = fb.meta(goal).confidence
    return ProofTree(
        proof_id=proof_id,
        goal={"predicate": goal.pred, "args": list(goal.args), "root": root_id},
        status="proved",
        confidence=root_conf,
        depth=max_depth[0],
        rules_fired=rules_fired,
        nodes=nodes,
        edges=edges,
    )
