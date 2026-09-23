"""
rag/synthesizer.py — HyperGraphRAG Reverse Synthesis Pipeline

Generates synthetic (question, context, ideal_answer) training pairs from
existing hyperedge data in HyperMesh DB — without any human annotation.

The "reverse synthesis" strategy:
  1. Pull batches of real hyperedges from the DB
  2. Generate diverse natural-language questions from templates (FREE — no LLM)
  3. Call OpenAI to generate ideal grounded answers given (context + question)
  4. Save in ChatML JSONL format ready for fine-tuning Phi-3, Qwen, or Llama-3

Estimated cost
--------------
  gpt-4o-mini: ~$0.001 per training pair
  3,000 examples: ~$3 total

Output format (standard OpenAI / Phi-3 / Qwen fine-tune JSONL)
--------------------------------------------------------------
  {"messages": [
    {"role": "system",    "content": "<system_prompt>"},
    {"role": "user",      "content": "<context>\\n\\n---\\n\\nQuestion: <question>"},
    {"role": "assistant", "content": "<ideal_answer>"}
  ]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .context    import ContextAssembler
from .retriever  import HyperGraphRetriever

log = logging.getLogger(__name__)


# ── Question templates ────────────────────────────────────────────────────────
# Each template is (question_text, required_entity_types, intent_tag)

_TEMPLATES: list[tuple[str, list[str], str]] = [
    # Entity-centric
    ("What entities co-occur with {entity} in these events?",
     ["machine", "process", "account", "entity"], "entity"),
    ("How frequently does {entity} appear and what does that indicate?",
     ["machine", "process", "account", "entity"], "entity"),
    ("What is the role of {entity} in the observed activity?",
     ["machine", "process", "account", "entity"], "entity"),
    ("List all entities that share events with {entity}.",
     ["machine", "process", "account", "entity"], "entity"),
    ("Is {entity} a hub, bridge, or peripheral entity? Explain.",
     ["machine", "process", "account", "entity"], "entity"),

    # Pattern-centric
    ("Which entities appear in the most events and what does that suggest?",
     [], "pattern"),
    ("Describe any dense co-occurrence clusters visible in these events.",
     [], "pattern"),
    ("What is the most suspicious pattern in these hyperedge events?",
     [], "pattern"),
    ("Identify any entities that connect otherwise separate groups.",
     [], "pattern"),
    ("What event formations repeat most often? What does that imply?",
     [], "pattern"),

    # Structural / analytical
    ("How many distinct entities are involved across these events?",
     [], "structural"),
    ("What is the average event size (number of co-occurring entities)?",
     [], "structural"),
    ("Which event involves the largest group of co-occurring entities?",
     [], "structural"),
    ("Describe the overall connectivity structure of these events.",
     [], "structural"),
    ("What fraction of events involve more than 3 entities simultaneously?",
     [], "structural"),

    # Temporal
    ("How does activity change across the observed time window?",
     [], "temporal"),
    ("Are there any time periods with unusually concentrated activity?",
     [], "temporal"),
    ("What was the first significant event in this dataset?",
     [], "temporal"),

    # Security / domain-specific
    ("Which entities show signs of lateral movement or pivoting?",
     [], "threat"),
    ("Are there any entities that appear to be coordinating their activity?",
     [], "threat"),
    ("What would an analyst prioritise for investigation from these events?",
     [], "threat"),
    ("Summarise the key findings from these hyperedge events in 3 bullet points.",
     [], "summary"),
]


# ── SynthesizerConfig ─────────────────────────────────────────────────────────

@dataclass
class SynthesizerConfig:
    openai_api_key: str   = ""
    model:          str   = "gpt-4o-mini"      # cheap + fast for answer generation
    base_url:       str   = "https://api.openai.com/v1"
    batch_size:     int   = 10                 # edges per context window
    n_questions:    int   = 3                  # questions generated per batch
    temperature:    float = 0.3                # slightly higher than RAG for diversity
    max_tokens:     int   = 600
    output_dir:     str   = "data/rag_training"
    seed:           int   = 42


# ── Training example ──────────────────────────────────────────────────────────

@dataclass
class TrainingExample:
    system_prompt: str
    user_message:  str
    answer:        str
    table:         str
    question_type: str
    n_edges:       int
    model:         str

    def to_chatml(self) -> dict[str, Any]:
        """OpenAI / Phi-3 / Qwen ChatML format."""
        return {
            "messages": [
                {"role": "system",    "content": self.system_prompt},
                {"role": "user",      "content": self.user_message},
                {"role": "assistant", "content": self.answer},
            ]
        }

    def to_alpaca(self) -> dict[str, Any]:
        """Alpaca instruction format (Llama-2 compatible)."""
        return {
            "instruction": self.system_prompt,
            "input":       self.user_message,
            "output":      self.answer,
        }


# ── ReverseHyperedgeSynthesizer ───────────────────────────────────────────────

class ReverseHyperedgeSynthesizer:
    """
    Generates synthetic fine-tuning data from an existing HyperMesh table.

    Parameters
    ----------
    db :
        Open ``hypermeshdb.Connection``.
    table :
        Table to synthesise from (e.g. "THREATEVENTS").
    entity_map :
        Entity label map ``{str(node_id): {type, display, short}}``.
    config :
        Synthesis configuration.
    db_dir :
        Base directory for saving output files.
    """

    def __init__(
        self,
        db:         Any,
        table:      str,
        entity_map: dict[str, Any] | None = None,
        config:     SynthesizerConfig | None = None,
        db_dir:     str = "data",
    ) -> None:
        self._db     = db
        self._table  = table.upper()
        self._em     = entity_map or {}
        self._cfg    = config or SynthesizerConfig()
        self._db_dir = db_dir
        self._rng    = random.Random(self._cfg.seed)
        self._assembler = ContextAssembler(self._table, token_budget=2000)

        out = Path(self._cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        self._out_path = out / f"{self._table.lower()}_training.jsonl"
        self._meta_path = out / f"{self._table.lower()}_meta.json"

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        n_batches: int = 50,
        progress_cb: Any | None = None,
    ) -> dict[str, Any]:
        """
        Run the full synthesis pipeline.

        Parameters
        ----------
        n_batches :
            Number of hyperedge batches to process. Each batch generates
            ``config.n_questions`` training pairs. Total examples ≈ n_batches × n_questions.
        progress_cb :
            Optional async callback(completed, total, example) for progress streaming.

        Returns
        -------
        dict with stats: examples_generated, cost_estimate, output_path.
        """
        t0 = time.perf_counter()
        log.info("Starting synthesis: table=%s, n_batches=%d", self._table, n_batches)

        # Load hypergraph
        try:
            an = self._db.analytics(self._table)
            hg = an.hypergraph
        except Exception as exc:
            raise ValueError(f"Cannot load table '{self._table}': {exc}") from exc

        retriever = HyperGraphRetriever(hg, self._em, top_k=self._cfg.batch_size)
        n_edges   = hg.B.shape[1]

        if n_edges == 0:
            return {"examples_generated": 0, "error": "Table is empty"}

        sys_prompt     = self._assembler.build_system_prompt()
        examples_written = 0
        total_prompt_tokens = 0
        failed = 0

        with open(self._out_path, "a", encoding="utf-8") as f_out:
            for batch_i in range(n_batches):
                # Random sample of edges for diversity
                sample_indices = self._rng.sample(
                    range(n_edges), min(self._cfg.batch_size, n_edges)
                )

                # Get entity IDs present in this batch for entity-type questions
                seed_entities = self._entities_in_batch(hg, sample_indices)

                # Build context for this batch
                edges = [retriever._build_edge(i, 1.0) for i in sample_indices]
                context, included_tags = self._assembler.build(
                    edges=edges, query_text=""
                )
                user_prefix = f"{context}\n\n---\n\nQuestion: "

                # Pick n_questions from templates
                questions = self._pick_questions(seed_entities, self._cfg.n_questions)

                for q_text, q_type in questions:
                    user_message = user_prefix + q_text
                    try:
                        answer, tokens = await self._generate_answer(
                            sys_prompt, user_message
                        )
                        total_prompt_tokens += tokens

                        ex = TrainingExample(
                            system_prompt = sys_prompt,
                            user_message  = user_message,
                            answer        = answer,
                            table         = self._table,
                            question_type = q_type,
                            n_edges       = len(edges),
                            model         = self._cfg.model,
                        )
                        f_out.write(json.dumps(ex.to_chatml(), ensure_ascii=False) + "\n")
                        examples_written += 1

                        if progress_cb:
                            await progress_cb(
                                completed  = batch_i * self._cfg.n_questions + examples_written % self._cfg.n_questions,
                                total      = n_batches * self._cfg.n_questions,
                                example    = {"question": q_text, "type": q_type, "batch": batch_i},
                            )

                    except Exception as exc:
                        log.warning("Batch %d question '%s' failed: %s", batch_i, q_text[:40], exc)
                        failed += 1

                # Small delay to respect rate limits
                if batch_i % 10 == 9:
                    await asyncio.sleep(0.5)

        elapsed    = time.perf_counter() - t0
        cost_est   = (total_prompt_tokens / 1_000_000) * 0.15  # gpt-4o-mini input pricing

        meta = {
            "table":              self._table,
            "examples_generated": examples_written,
            "batches":            n_batches,
            "failed":             failed,
            "elapsed_s":          round(elapsed, 1),
            "model":              self._cfg.model,
            "cost_estimate_usd":  round(cost_est, 4),
            "output_path":        str(self._out_path),
            "timestamp":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        log.info("Synthesis complete: %d examples, ~$%.3f, %s", examples_written, cost_est, self._out_path)
        return meta

    def stats(self) -> dict[str, Any]:
        """Return current output file stats."""
        count = 0
        if self._out_path.exists():
            with open(self._out_path, encoding="utf-8") as f:
                count = sum(1 for _ in f)
        meta: dict[str, Any] = {}
        if self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text("utf-8"))
        return {
            "table":             self._table,
            "examples":          count,
            "output_path":       str(self._out_path),
            "last_run_meta":     meta,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _entities_in_batch(self, hg: Any, edge_indices: list[int]) -> list[tuple[str, str]]:
        """Return (label, type) for entities present in the batch."""
        import numpy as np
        seen: set[int] = set()
        result: list[tuple[str, str]] = []
        for eidx in edge_indices:
            rows = hg.B.getcol(eidx).nonzero()[0]
            for r in rows:
                nid = int(hg.node_ids[r])
                if nid not in seen:
                    seen.add(nid)
                    meta  = self._em.get(str(nid), {})
                    label = meta.get("short") or meta.get("display") or f"node_{nid}"
                    etype = meta.get("type", "entity")
                    result.append((label, etype))
        return result

    def _pick_questions(
        self,
        entities: list[tuple[str, str]],
        n: int,
    ) -> list[tuple[str, str]]:
        """Pick n diverse question templates, filling in entity slots."""
        chosen: list[tuple[str, str]] = []
        shuffled = self._rng.sample(_TEMPLATES, min(n * 4, len(_TEMPLATES)))

        for template, required_types, intent in shuffled:
            if len(chosen) >= n:
                break

            if "{entity}" in template:
                # Need a matching entity
                matching = [(l, t) for l, t in entities if not required_types or t in required_types]
                if not matching:
                    continue
                label, _ = self._rng.choice(matching)
                question = template.format(entity=label)
            else:
                question = template

            chosen.append((question, intent))

        # Pad with summary questions if needed
        while len(chosen) < n:
            chosen.append(("Summarise the key patterns in these events.", "summary"))

        return chosen[:n]

    async def _generate_answer(self, sys_prompt: str, user_message: str) -> tuple[str, int]:
        """Call OpenAI to generate an ideal answer. Returns (answer, prompt_tokens)."""
        from openai import AsyncOpenAI

        api_key = self._cfg.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        client  = AsyncOpenAI(api_key=api_key, base_url=self._cfg.base_url, timeout=60)

        response = await client.chat.completions.create(
            model       = self._cfg.model,
            messages    = [
                {"role": "system", "content": sys_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature = self._cfg.temperature,
            max_tokens  = self._cfg.max_tokens,
        )
        answer = response.choices[0].message.content or ""
        tokens = response.usage.prompt_tokens if response.usage else 0
        return answer, tokens

    def export_alpaca(self, out_path: str | None = None) -> str:
        """Convert ChatML JSONL to Alpaca format (for Llama-2 fine-tuning)."""
        alpaca_path = out_path or str(self._out_path).replace(".jsonl", "_alpaca.jsonl")
        with open(self._out_path, encoding="utf-8") as f_in, \
             open(alpaca_path, "w", encoding="utf-8") as f_out:
            for line in f_in:
                chatml = json.loads(line)
                msgs   = chatml["messages"]
                ex = TrainingExample(
                    system_prompt = msgs[0]["content"],
                    user_message  = msgs[1]["content"],
                    answer        = msgs[2]["content"],
                    table="", question_type="", n_edges=0, model="",
                )
                f_out.write(json.dumps(ex.to_alpaca(), ensure_ascii=False) + "\n")
        return alpaca_path
