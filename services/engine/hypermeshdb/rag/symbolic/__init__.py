"""
hypermeshdb.rag.symbolic — Neuro-Symbolic reasoning layer for HyperGraphRAG.

A deterministic, LLM-free reasoning stratum that sits between retrieval and
generation:

    retrieve → [facts_from_edges] → SymbolicReasoner (rules) → derived facts
             → build_proof (verifiable provenance) → LLM → HallucinationFirewall

Public surface
--------------
- ``parse_rule`` / ``Rule`` / ``RuleValidationError`` — rule schema + safety.
- ``RuleStore``                                       — durable rule registry.
- ``facts_from_edges`` / ``FactBase`` / ``Fact``      — fact base + edge bridge.
- ``SymbolicReasoner`` / ``ReasonResult`` / ``stratify`` — forward chaining.
- ``build_proof`` / ``ProofTree``                     — proof + provenance.
- ``HallucinationFirewall`` / ``FirewallResult``      — answer gating.
"""
from .schema import (
    Atom, Compare, Head, Neg, Rule, RuleValidationError, EDB_PREDS, is_var, parse_rule,
)
from .facts import Fact, FactBase, FactMeta, facts_from_edges
from .reasoner import (
    SymbolicReasoner, ReasonResult, StratificationError, stratify, match_body,
)
from .proof import ProofTree, build_proof
from .firewall import HallucinationFirewall, FirewallResult
from .store import RuleStore

__all__ = [
    "Atom", "Compare", "Head", "Neg", "Rule", "RuleValidationError", "EDB_PREDS",
    "is_var", "parse_rule",
    "Fact", "FactBase", "FactMeta", "facts_from_edges",
    "SymbolicReasoner", "ReasonResult", "StratificationError", "stratify", "match_body",
    "ProofTree", "build_proof",
    "HallucinationFirewall", "FirewallResult",
    "RuleStore",
]
