"""
rag/pipeline.py — HyperGraphRAG Orchestration Pipeline

Wires together QueryParser → HyperGraphRetriever → ContextAssembler → LLMGenerator
into a single async ``query()`` call.

Usage
-----
    from hypermeshdb.rag import RAGPipeline
    import hypermeshdb

    db   = hypermeshdb.connect("data/")
    pipe = RAGPipeline(db, table="THREATEVENTS", openai_api_key="sk-...")
    result = await pipe.query("What did Account-admin do last week?")

    print(result.answer)
    for e in result.cited_edges:
        print(f"  [{e.hedge_tag()}] {e.member_labels}")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .query_parser import QueryParser, ParsedQuery
from .retriever    import HyperGraphRetriever, RetrievedEdge
from .context      import ContextAssembler
from .generator    import LLMGenerator, LLMConfig, GenerationResult
from .harvester    import InteractionHarvester
from .symbolic     import (
    SymbolicReasoner, HallucinationFirewall, RuleStore, facts_from_edges,
    build_proof, parse_rule,
)
from .symbolic.facts import Fact

log = logging.getLogger(__name__)

VALID_MODES = ("neuro", "symbolic", "hybrid")


# ── RAGResult ─────────────────────────────────────────────────────────────────

@dataclass
class RAGResult:
    query:          str
    parsed:         ParsedQuery
    answer:         str
    cited_edges:    list[RetrievedEdge]    = field(default_factory=list)
    all_edges:      list[RetrievedEdge]    = field(default_factory=list)
    confidence:     float                  = 1.0
    low_confidence: bool                   = False
    model:          str                    = ""
    prompt_tokens:  int                    = 0
    completion_tokens: int                 = 0
    retrieval_ms:   float                  = 0.0
    generation_ms:  float                  = 0.0
    total_ms:       float                  = 0.0
    table:          str                    = ""
    context_tokens:  int                   = 0
    interaction_id:  str                   = ""
    # ── Neuro-symbolic extensions ──
    mode:           str                    = "neuro"
    abstained:      bool                   = False
    derived_facts:  list[dict]             = field(default_factory=list)
    proofs:         list[dict]             = field(default_factory=list)
    firewall:       dict                   = field(default_factory=dict)
    rules_fired:    list[str]              = field(default_factory=list)
    reasoning_ms:   float                  = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query":             self.query,
            "answer":            self.answer,
            "table":             self.table,
            "mode":              self.mode,
            "abstained":         self.abstained,
            "parsed_query":      self.parsed.as_dict(),
            "cited_edges":       [e.as_dict() for e in self.cited_edges],
            "all_edges":         [e.as_dict() for e in self.all_edges[:20]],  # cap for response size
            "derived_facts":     self.derived_facts,
            "proofs":            self.proofs,
            "firewall":          self.firewall,
            "rules_fired":       self.rules_fired,
            "confidence":        round(self.confidence, 3),
            "low_confidence":    self.low_confidence,
            "model":             self.model,
            "prompt_tokens":     self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retrieval_ms":      round(self.retrieval_ms, 1),
            "reasoning_ms":      round(self.reasoning_ms, 1),
            "generation_ms":     round(self.generation_ms, 1),
            "total_ms":          round(self.total_ms, 1),
            "context_tokens":    self.context_tokens,
            "interaction_id":    self.interaction_id,
        }


# ── RAGPipeline ───────────────────────────────────────────────────────────────

class RAGPipeline:
    """
    End-to-end HyperGraphRAG pipeline.

    Parameters
    ----------
    db :
        An open ``hypermeshdb.Connection``.
    table :
        The hyperedge table to query (e.g. "THREATEVENTS", "DAWN").
    entity_map :
        Dict ``{str(node_id): {type, display, short}}``.
        If None, loaded automatically from ``{db_dir}/{TABLE}_entity_map.json``.
    openai_api_key :
        OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
    llm_config :
        Full LLMConfig override (model, base_url, etc.).
        If None, uses gpt-4o-mini via OpenAI API.
    top_k :
        Maximum hyperedges to retrieve per query.
    token_budget :
        Max context tokens for the hyperedge block.
    db_dir :
        Database directory (used to locate entity maps).
    """

    def __init__(
        self,
        db:              Any,                       # hypermeshdb.Connection
        table:           str,
        entity_map:      dict[str, Any] | None = None,
        openai_api_key:  str | None            = None,
        llm_config:      LLMConfig | None      = None,
        top_k:           int                   = 40,
        token_budget:    int                   = 3000,
        db_dir:          str                   = "",
        # ── Neuro-symbolic ──
        mode:            str                   = "neuro",
        require_proof:   bool                  = False,
        rules:           list[dict] | None     = None,
        rule_store:      RuleStore | None      = None,
        max_reason_iterations: int             = 64,
        reason_deadline_ms:    float | None    = None,
        provenance_resolver:   Any             = None,
    ) -> None:
        self._db     = db
        self._table  = table.upper()
        self._db_dir = db_dir or os.environ.get("HMDB_DIR", "data")
        self._top_k  = top_k

        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
        self._mode          = mode
        self._require_proof = require_proof
        self._prov_resolver = provenance_resolver

        # Entity map
        self._em = entity_map or self._load_entity_map(self._table, self._db_dir)

        # LLM config
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        if llm_config:
            self._cfg = llm_config
        else:
            self._cfg = LLMConfig(api_key=api_key)

        # Sub-components
        self._parser    = QueryParser(self._em)
        self._assembler = ContextAssembler(self._table, token_budget)
        self._generator = LLMGenerator(self._cfg)
        self._harvester = InteractionHarvester(self._db_dir)
        self._firewall  = HallucinationFirewall()

        # Rule source: explicit list wins, else the durable store.
        self._rule_store = rule_store if rule_store is not None else RuleStore(self._db_dir)
        self._ad_hoc_rules = [parse_rule(r) for r in rules] if rules else None
        self._reasoner = SymbolicReasoner(
            max_iterations=max_reason_iterations, deadline_ms=reason_deadline_ms,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def query(self, query_text: str) -> RAGResult:
        """Full pipeline: NL query → grounded answer with provenance."""
        t0 = time.perf_counter()

        # 1. Parse
        parsed = self._parser.parse(query_text)
        log.debug("Parsed query: entities=%s intent=%s time=%s-%s",
                  parsed.entities, parsed.intent, parsed.time_start, parsed.time_end)

        # 2. Load hypergraph and retrieve
        t_ret = time.perf_counter()
        try:
            an = self._db.analytics(self._table)
            hg = an.hypergraph
        except Exception as exc:
            raise ValueError(f"Table '{self._table}' not found: {exc}") from exc

        retriever = HyperGraphRetriever(hg, self._em, top_k=self._top_k)
        node_ids  = [nid for _, nid in parsed.entities if nid is not None]

        edges = retriever.retrieve(
            node_ids   = node_ids or None,
            time_start = parsed.time_start,
            time_end   = parsed.time_end,
            keywords   = parsed.keywords,
            min_weight = parsed.raw_filters.get("min_weight"),
            top_k      = parsed.raw_filters.get("top_n", self._top_k),
        )
        retrieval_ms = (time.perf_counter() - t_ret) * 1000

        # 3. Symbolic reasoning (neuro-symbolic core) — LLM-free, deterministic.
        t_reason = time.perf_counter()
        derived_descriptors, proof_dicts, rules_fired, proved_tags = \
            self._reason(edges)
        reasoning_ms = (time.perf_counter() - t_reason) * 1000

        # 4. Run pattern detection to enrich context (fast path only)
        findings: list[dict] = []
        try:
            from hypermeshdb.patterns.detector import PatternDetector
            detector = PatternDetector(an, entity_map=self._em, table_name=self._table)
            det_result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: detector.detect(full_scan=False)
            )
            # Label entity IDs in findings with human-readable names
            findings = self._label_findings(det_result.as_dict()["findings"])
        except Exception as exc:
            log.debug("Pattern detection skipped: %s", exc)

        # 5. Assemble context (symbolic facts get citation priority)
        context, included_tags = self._assembler.build(
            edges      = edges,
            time_start = parsed.time_start,
            time_end   = parsed.time_end,
            query_text = query_text,
            findings   = findings,
            derived    = derived_descriptors,
        )
        # Allowed citation tags for the firewall = everything in context + proofs.
        pattern_tags = [f"PATTERN-{i}" for i in range(len(findings))]
        allowed_tags = set(included_tags) | set(proved_tags) | set(pattern_tags)
        stats = self._assembler.stats(context, included_tags)
        proved = bool(derived_descriptors)

        # 6. Generate (skipped in pure-symbolic mode)
        t_gen = time.perf_counter()
        if self._mode == "symbolic":
            raw_answer        = self._render_symbolic_answer(query_text, derived_descriptors)
            gen_confidence    = self._symbolic_confidence(derived_descriptors)
            model_name        = "symbolic"
            prompt_tokens     = 0
            completion_tokens = stats["approx_tokens"]
            cited_ids: list[int] = []
        else:
            sys_prompt = self._assembler.build_system_prompt()
            gen_result: GenerationResult = await self._generator.generate(
                system_prompt = sys_prompt,
                context       = context,
                user_query    = query_text,
                included_tags = list(allowed_tags),
            )
            raw_answer        = gen_result.answer
            gen_confidence    = gen_result.confidence
            model_name        = gen_result.model
            prompt_tokens     = gen_result.prompt_tokens
            completion_tokens = gen_result.completion_tokens
            cited_ids         = gen_result.cited_edge_ids
        generation_ms = (time.perf_counter() - t_gen) * 1000

        # 7. Hallucination firewall — gate the answer against proof/evidence.
        #    `proved` is strict: a symbolic conclusion was actually derived.
        fw = self._firewall.check(
            raw_answer, allowed_tags,
            proved        = proved,
            require_proof = self._require_proof,
        )
        answer = fw.answer

        # 8. Map cited edge indices back to RetrievedEdge objects
        edge_by_idx = {e.edge_idx: e for e in edges}
        cited_edges = [edge_by_idx[i] for i in cited_ids if i in edge_by_idx]

        total_ms = (time.perf_counter() - t0) * 1000

        # 9. Harvest interaction for training data
        interaction_id = ""
        try:
            interaction_id = self._harvester.save(
                table         = self._table,
                query         = query_text,
                answer        = answer,
                context       = context,
                system_prompt = self._assembler.build_system_prompt(),
                model         = model_name,
                confidence    = gen_confidence,
                cited_edges   = cited_ids,
            )
        except Exception as exc:
            log.debug("Harvester save failed (non-fatal): %s", exc)

        return RAGResult(
            query             = query_text,
            parsed            = parsed,
            answer            = answer,
            cited_edges       = cited_edges,
            all_edges         = edges,
            confidence        = 0.0 if fw.abstained else gen_confidence,
            low_confidence    = fw.abstained or gen_confidence < 0.5,
            model             = model_name,
            prompt_tokens     = prompt_tokens,
            completion_tokens = completion_tokens,
            retrieval_ms      = retrieval_ms,
            reasoning_ms      = reasoning_ms,
            generation_ms     = generation_ms,
            total_ms          = total_ms,
            table             = self._table,
            context_tokens    = stats["approx_tokens"],
            interaction_id    = interaction_id,
            mode              = self._mode,
            abstained         = fw.abstained,
            derived_facts     = derived_descriptors,
            proofs            = proof_dicts,
            firewall          = fw.as_dict(),
            rules_fired       = rules_fired,
        )

    async def stream(self, query_text: str) -> tuple[ParsedQuery, list[RetrievedEdge], AsyncIterator[str]]:
        """
        Streaming version.

        Returns (parsed_query, retrieved_edges, token_stream).
        Call ``async for token in token_stream`` to consume the answer.
        """
        parsed = self._parser.parse(query_text)

        an = self._db.analytics(self._table)
        hg = an.hypergraph
        retriever = HyperGraphRetriever(hg, self._em, top_k=self._top_k)
        node_ids  = [nid for _, nid in parsed.entities if nid is not None]

        edges = retriever.retrieve(
            node_ids   = node_ids or None,
            time_start = parsed.time_start,
            time_end   = parsed.time_end,
            keywords   = parsed.keywords,
        )

        context, included_tags = self._assembler.build(
            edges, parsed.time_start, parsed.time_end, query_text
        )
        sys_prompt = self._assembler.build_system_prompt()

        raw_stream = self._generator.stream(sys_prompt, context, query_text)

        async def verified_stream() -> AsyncIterator[str]:
            """Buffer generation until citations can be verified.

            Returning unverified tokens and retracting them later is not a
            security control. Production callers therefore receive one
            sanitized chunk after generation completes. A future sentence-level
            protocol can restore incremental display without weakening this
            boundary.
            """
            chunks: list[str] = []
            async for chunk in raw_stream:
                chunks.append(chunk)
            checked = self._firewall.check(
                "".join(chunks), set(included_tags), proved=False,
                require_proof=False,
            )
            yield checked.answer

        return parsed, edges, verified_stream()

    # ── Neuro-symbolic helpers ──────────────────────────────────────────────────

    def _reason(
        self, edges: list[RetrievedEdge],
    ) -> tuple[list[dict], list[dict], list[str], set[str]]:
        """Run the symbolic reasoner over retrieved edges → (descriptors, proofs,
        rules_fired, proved_tags). Returns empties for mode='neuro' or no rules."""
        if self._mode == "neuro":
            return [], [], [], set()
        try:
            rules = (self._ad_hoc_rules if self._ad_hoc_rules is not None
                     else self._rule_store.enabled_rules())
        except Exception as exc:
            log.debug("Rule load failed (non-fatal): %s", exc)
            rules = []
        if not rules:
            return [], [], [], set()

        fb  = facts_from_edges(edges, table=self._table)
        res = self._reasoner.run(fb, rules)
        # Deterministic ordering of exposed conclusions.
        derived = sorted(res.derived, key=lambda f: (f.pred, tuple(str(a) for a in f.args)))

        rule_tag_map: dict[str, str] = {}
        def rtag(rid: str | None) -> str:
            if not rid:
                return ""
            if rid not in rule_tag_map:
                rule_tag_map[rid] = f"RULE-{len(rule_tag_map)}"
            return rule_tag_map[rid]

        descriptors: list[dict] = []
        proof_dicts: list[dict] = []
        proved_tags: set[str]   = set()

        for i, f in enumerate(derived[:50], start=1):
            meta  = fb.meta(f)
            proof = build_proof(f, fb, proof_id=f"proof-{i}",
                                provenance_resolver=self._prov_resolver)
            pdict = proof.as_dict()
            hedge_tags = sorted({n.get("hedge_tag") for n in pdict["nodes"]
                                 if n.get("hedge_tag")})
            step_tag = f"STEP-{i}"
            rule_tag = rtag(meta.rule_id)
            descriptors.append({
                "step_tag":   step_tag,
                "fact":       str(f),
                "predicate":  f.pred,
                "args":       list(f.args),
                "confidence": round(meta.confidence, 4),
                "rule_id":    meta.rule_id,
                "rule_tag":   rule_tag,
                "hedge_tags": hedge_tags,
            })
            pdict["step_tag"] = step_tag
            pdict["rule_tag"] = rule_tag
            proof_dicts.append(pdict)
            proved_tags.add(step_tag)
            if rule_tag:
                proved_tags.add(rule_tag)
            proved_tags.update(hedge_tags)

        return descriptors, proof_dicts, list(res.rules_fired), proved_tags

    def _render_symbolic_answer(self, query_text: str, descriptors: list[dict]) -> str:
        """Deterministic answer for mode='symbolic' (no LLM)."""
        if not descriptors:
            return "[UNVERIFIED: cannot determine from available data]"
        lines = [f"Answer to: {query_text.strip()}", ""]
        for d in descriptors[:24]:
            supp = "".join(f"[{t}]" for t in (d.get("hedge_tags") or [])[:6])
            rt   = f"[{d['rule_tag']}]" if d.get("rule_tag") else ""
            lines.append(
                f"{d['fact']} is established by rule {d['rule_id']} "
                f"{rt}[{d['step_tag']}]{supp} (confidence {d['confidence']:.2f})."
            )
        sources = ", ".join(f"[{d['step_tag']}]" for d in descriptors[:24])
        lines.append("")
        lines.append(f"Sources: {sources}")
        return "\n".join(lines)

    @staticmethod
    def _symbolic_confidence(descriptors: list[dict]) -> float:
        if not descriptors:
            return 0.0
        return min(float(d.get("confidence", 1.0)) for d in descriptors)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _label_findings(self, findings: list[dict]) -> list[dict]:
        """Replace raw node IDs in findings with human-readable labels."""
        import copy
        result = []
        for f in findings:
            fd = copy.deepcopy(f)
            raw_nodes  = fd.get("affected_nodes", [])
            if raw_nodes:
                labeled = []
                for nid in raw_nodes[:8]:
                    meta  = self._em.get(str(nid), {})
                    label = meta.get("short") or meta.get("display") or f"node_{nid}"
                    labeled.append(f"{label} (id={nid})")
                fd["affected_nodes_labeled"] = labeled
                # Inject labels into description for LLM clarity
                fd["description"] = (
                    fd.get("description", "") +
                    f" Key entities: {', '.join(labeled[:5])}."
                )
            result.append(fd)
        return result

    @staticmethod
    def _load_entity_map(table: str, db_dir: str) -> dict[str, Any]:
        safe = table.upper().replace(" ", "_")
        candidates = [
            os.path.join(db_dir, f"{safe}_entity_map.json"),
            os.path.join("data", f"{safe}_entity_map.json"),
            os.path.join("data", "threat_entity_map.json"),
        ]
        for path in candidates:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @property
    def table(self) -> str:
        return self._table

    @property
    def model(self) -> str:
        return self._cfg.model
