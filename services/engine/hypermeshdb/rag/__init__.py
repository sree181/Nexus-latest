"""
hypermeshdb.rag — HyperMesh HyperGraphRAG

Retrieval-Augmented Generation using native temporal hypergraph indexes
as the retrieval primitive. No construction cost — the hyperedges ARE
the knowledge store.

Pipeline
--------
1. QueryParser     — NL → {entities, time_window, intent, filters}
2. Retriever       — FMI + TPI → ranked hyperedges
3. ContextAssembler — hyperedges → SLM-ready prompt with [HEDGE-N] tags
4. LLMGenerator    — OpenAI / local → grounded answer with citations
5. RAGPipeline     — orchestrates all four steps

Quick start
-----------
    from hypermeshdb.rag import RAGPipeline
    from hypermeshdb import connect

    db   = connect("data/")
    pipe = RAGPipeline(db, table="THREATEVENTS", openai_api_key="sk-...")
    result = await pipe.query("What machines did Account-admin touch last week?")
    print(result.answer)
    print(result.cited_edges)
"""

from .pipeline    import RAGPipeline, RAGResult
from .query_parser import QueryParser, ParsedQuery
from .retriever   import HyperGraphRetriever, RetrievedEdge
from .context     import ContextAssembler
from .generator   import LLMGenerator, LLMConfig
from .synthesizer import ReverseHyperedgeSynthesizer, SynthesizerConfig, TrainingExample
from .harvester   import InteractionHarvester
from .registry    import ModelRegistry
from .symbolic    import (
    Rule, RuleValidationError, parse_rule, RuleStore,
    SymbolicReasoner, ReasonResult, stratify, StratificationError,
    Fact, FactBase, facts_from_edges,
    ProofTree, build_proof,
    HallucinationFirewall, FirewallResult,
)

__all__ = [
    "RAGPipeline", "RAGResult",
    "QueryParser", "ParsedQuery",
    "HyperGraphRetriever", "RetrievedEdge",
    "ContextAssembler",
    "LLMGenerator", "LLMConfig",
    "ReverseHyperedgeSynthesizer", "SynthesizerConfig", "TrainingExample",
    "InteractionHarvester",
    "ModelRegistry",
    # ── Neuro-symbolic ──
    "Rule", "RuleValidationError", "parse_rule", "RuleStore",
    "SymbolicReasoner", "ReasonResult", "stratify", "StratificationError",
    "Fact", "FactBase", "facts_from_edges",
    "ProofTree", "build_proof",
    "HallucinationFirewall", "FirewallResult",
]
