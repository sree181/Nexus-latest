"""
rag/context.py — Context Assembler for HyperGraphRAG

Converts retrieved hyperedges into a structured prompt context that an SLM
can reason over. Each hyperedge is tagged with a [HEDGE-N] citation marker
so the generator can produce grounded, traceable answers.

Token budget
------------
Targets ~3000 tokens for the hyperedge context block (leaving room for the
system prompt, user query, and generated response within a 4K–8K context
window). Deduplicates edges whose member sets are >70% identical (Jaccard).

Output format
-------------
=== HYPEREDGE EVENTS (Table: THREATEVENTS) ===
Time window: 2024-01-15 00:00 UTC → 2024-01-22 23:59 UTC
Retrieved: 23 events | Shown: 15 (budget limit)

[HEDGE-4491] 2024-01-16 02:14:00 UTC | EVENT | weight=0.95 | 5 members
  Entities: MACHINE-7 (machine), svchost.exe (process), MACHINE-3 (machine),
            MACHINE-12 (machine), ACCOUNT-ADMIN (account)

[HEDGE-4502] ...
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .retriever import RetrievedEdge


# ── Token estimation (rough: 1 token ≈ 4 chars for English prose) ────────────

def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── Jaccard deduplication ─────────────────────────────────────────────────────

def _jaccard(a: list[int], b: list[int]) -> float:
    sa, sb = set(a), set(b)
    inter  = len(sa & sb)
    union  = len(sa | sb)
    return inter / union if union else 0.0


def _deduplicate(edges: list[RetrievedEdge], threshold: float = 0.75) -> list[RetrievedEdge]:
    """Remove near-duplicate edges (Jaccard similarity > threshold)."""
    kept: list[RetrievedEdge] = []
    for edge in edges:
        duplicate = False
        for k in kept:
            if _jaccard(edge.member_ids, k.member_ids) > threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(edge)
    return kept


# ── Timestamp formatting ──────────────────────────────────────────────────────

def _fmt_ts(epoch: int) -> str:
    if epoch <= 0:
        return f"ts={epoch}"
    try:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, OverflowError):
        return f"ts={epoch}"


# ── ContextAssembler ──────────────────────────────────────────────────────────

class ContextAssembler:
    """
    Builds the hyperedge context block for the SLM prompt.

    Parameters
    ----------
    table_name :
        Name of the source hyperedge table.
    token_budget :
        Approximate maximum tokens for the context block.
    dedup_threshold :
        Jaccard similarity above which two edges are considered duplicates.
    """

    def __init__(
        self,
        table_name:      str   = "UNKNOWN",
        token_budget:    int   = 3000,
        dedup_threshold: float = 0.75,
    ) -> None:
        self._table          = table_name.upper()
        self._budget         = token_budget
        self._dedup_threshold = dedup_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def build(
        self,
        edges:      list[RetrievedEdge],
        time_start: int | None = None,
        time_end:   int | None = None,
        query_text: str = "",
        findings:   list[dict] | None = None,
        derived:    list[dict] | None = None,
    ) -> tuple[str, list[str]]:
        """
        Build the context string and list of citation tags included.

        Returns
        -------
        (context_block, tags_included)  — tags include STEP-/RULE-/HEDGE-/PATTERN-.
        """
        # Deduplicate
        edges = _deduplicate(edges, self._dedup_threshold)

        # Symbolic (derived facts) — highest priority, always retained.
        symbolic_block = self.build_symbolic_section(derived or [])
        symbolic_tags: list[str] = []
        for d in (derived or []):
            if d.get("step_tag"):
                symbolic_tags.append(d["step_tag"])
            if d.get("rule_tag"):
                symbolic_tags.append(d["rule_tag"])
            symbolic_tags.extend(d.get("hedge_tags", []) or [])

        # Prepend pattern findings if provided
        pattern_block = self.build_pattern_section(findings or [])

        # Build header
        ts_str    = ""
        if time_start or time_end:
            t0 = _fmt_ts(time_start) if time_start else "beginning"
            t1 = _fmt_ts(time_end)   if time_end   else "now"
            ts_str = f"\nTime window: {t0} → {t1}"

        header = (
            f"=== HYPEREDGE EVENTS (Table: {self._table}) ==={ts_str}\n"
            f"Retrieved: {len(edges)} events\n"
        )

        # Build edge blocks within token budget
        budget_remaining = self._budget - _approx_tokens(header)
        blocks: list[str]  = []
        included_tags: list[str] = []

        for edge in edges:
            block = self._format_edge(edge)
            cost  = _approx_tokens(block)
            if budget_remaining - cost < 100:
                break   # stop before running over budget
            blocks.append(block)
            included_tags.append(edge.hedge_tag())
            budget_remaining -= cost

        # Append summary note
        shown_note = f"Showing {len(blocks)} of {len(edges)} events"
        if len(blocks) < len(edges):
            shown_note += f" (budget limit: ~{self._budget} tokens)"
        shown_note += "\n"

        hedge_block = header + shown_note + "\n" + "\n".join(blocks)
        context = (pattern_block + "\n" + hedge_block) if pattern_block else hedge_block
        if symbolic_block:
            context = symbolic_block + "\n" + context

        # Symbolic tags first so they take citation priority; de-dup preserving order.
        all_tags = list(dict.fromkeys(symbolic_tags + included_tags))
        return context, all_tags

    def build_system_prompt(self) -> str:
        return (
            "You are HyperMesh RAG — an expert hypergraph analyst. "
            "You answer questions by reasoning ONLY over the structured data provided below.\n\n"
            "DATA FORMAT:\n"
            "- DERIVED FACTS: conclusions proven by the symbolic reasoner from the rule "
            "base. Each is tagged [STEP-N] and names the rule [RULE-N] that produced it. "
            "These are GUARANTEED true given their cited evidence — prefer them.\n"
            "- DETECTED PATTERNS: structural anomalies found by automated analysis. "
            "Each is tagged [PATTERN-N].\n"
            "- HYPEREDGE EVENTS: raw co-occurrence events. Each is tagged [HEDGE-N].\n\n"
            "MANDATORY CITATION RULES — follow these exactly or your answer is invalid:\n"
            "1. Every sentence containing a factual claim MUST end with one or more citation tags.\n"
            "   Good: 'Edge 10 is a coordinated threat [STEP-1][RULE-0][HEDGE-10].'\n"
            "   Bad:  'azrcipreplcymlt is a hub.' (no citation — forbidden)\n"
            "2. Use [STEP-N]/[RULE-N] for claims that follow from a derived fact.\n"
            "3. Use [PATTERN-N] for claims derived from detected patterns.\n"
            "4. Use [HEDGE-N] for claims derived from specific hyperedge events.\n"
            "5. If a claim cannot be supported by any provided data, write exactly: "
            "'[UNVERIFIED: cannot determine from available data]'\n"
            "6. Do NOT use external knowledge. Every fact must come from the data below.\n"
            "7. At the very end of your response, write a sources line:\n"
            "   'Sources: [STEP-1], [HEDGE-12], [HEDGE-47]'\n\n"
            "ANSWER STYLE: concise, analytical. "
            "Lead with the most important finding. Use bullet points for lists of entities."
        )

    def build_symbolic_section(self, derived: list[dict]) -> str:
        """Format reasoner-derived facts as a citable, high-priority context block."""
        if not derived:
            return ""
        lines = ["=== DERIVED FACTS (symbolic reasoning — proven, prefer these) ==="]
        for d in derived[:24]:
            step = d.get("step_tag", "STEP-?")
            rule = d.get("rule_tag", "")
            conf = d.get("confidence", 1.0)
            fact = d.get("fact", "")
            rule_id = d.get("rule_id", "")
            support = d.get("hedge_tags", []) or []
            rcite = f"[{rule}]" if rule else ""
            lines.append(
                f"\n[{step}] {fact} {rcite} (rule: {rule_id}, confidence={conf:.2f})"
            )
            if support:
                lines.append(f"  Supported by: {', '.join('[' + t + ']' for t in support[:12])}")
        lines.append("")
        return "\n".join(lines)

    def build_pattern_section(self, findings: list[dict]) -> str:
        """Format PatternDetector findings as a citable context section."""
        if not findings:
            return ""
        lines = ["=== DETECTED PATTERNS (automated structural analysis) ==="]
        for i, f in enumerate(findings[:8]):
            sev   = f.get("severity", "info").upper()
            title = f.get("title", "Unknown pattern")
            desc  = f.get("description", "")
            nodes = f.get("affected_nodes", [])
            lines.append(
                f"\n[PATTERN-{i}] [{sev}] {title}\n"
                f"  Analysis: {desc[:300]}{'...' if len(desc) > 300 else ''}"
            )
            if nodes:
                lines.append(f"  Affected node IDs: {nodes[:10]}")
        lines.append("")
        return "\n".join(lines)

    # ── Formatting ────────────────────────────────────────────────────────────

    def _format_edge(self, edge: RetrievedEdge) -> str:
        ts_str = _fmt_ts(edge.timestamp)

        # Members line — group by type
        members_str = self._format_members(edge)

        # Properties line (only non-empty)
        props_str = ""
        if edge.properties:
            prop_parts = [f"{k}={v}" for k, v in list(edge.properties.items())[:6]]
            props_str = f"\n  Properties: {', '.join(prop_parts)}"

        return (
            f"[{edge.hedge_tag()}] {ts_str} | {edge.formation} | "
            f"weight={edge.weight:.3f} | {edge.size} entities\n"
            f"  Entities: {members_str}"
            f"{props_str}"
        )

    def _format_members(self, edge: RetrievedEdge) -> str:
        parts: list[str] = []
        for label, etype in zip(edge.member_labels, edge.member_types):
            parts.append(f"{label} ({etype})")
        if not parts:
            return f"{edge.size} entities"
        # Wrap long lists
        if len(parts) <= 5:
            return ", ".join(parts)
        first_5 = ", ".join(parts[:5])
        rest    = len(parts) - 5
        return f"{first_5}, +{rest} more"

    # ── Statistics for logging ────────────────────────────────────────────────

    def stats(self, context: str, tags: list[str]) -> dict[str, Any]:
        return {
            "approx_tokens":  _approx_tokens(context),
            "n_edges":        len(tags),
            "hedge_tags":     tags,
        }
