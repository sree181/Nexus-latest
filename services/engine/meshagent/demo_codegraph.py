"""
Project:     MeshAgent
File:        meshagent/demo_codegraph.py
Description: Builds a curated but AUTHENTIC code-provenance graph: a small
             transformer written as real Python, with its classes and
             packages extracted by the same AST capture layer a live
             session uses, and its design decisions attached to real
             sources — including one poisoned source, so the forget /
             blast-radius interaction has something true to light up.
             `build()` returns the export JSON the visualization consumes.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from typing import Any

from hypermeshdb.agentmem import MemoryStore

from .codegraph import CodeGraphRecorder
from .sarif import ingest_sarif

# A CodeQL-shaped SARIF result that traced untrusted input all the way to the
# UnsafeShardLoader's pickle.load: an HTTP request body flows into the shard
# path and reaches the deserializer. The codeFlow IS the reachability proof,
# so ingesting it upgrades that sink from "present" to "exploitable".
_SARIF_RUN: dict[str, Any] = {
    "version": "2.1.0",
    "runs": [{
        "tool": {"driver": {"name": "CodeQL", "rules": [{
            "id": "py/unsafe-deserialization",
            "properties": {"tags": ["security", "external/cwe/cwe-502"],
                           "security-severity": "9.8"},
        }]}},
        "results": [{
            "ruleId": "py/unsafe-deserialization",
            "message": {"text": "Untrusted data from an HTTP request body "
                        "reaches pickle.load in UnsafeShardLoader."},
            "codeFlows": [{"threadFlows": [{"locations": [
                {"location": {"physicalLocation": {
                    "artifactLocation": {"uri": "api/serve.py"},
                    "region": {"startLine": 19}}}},
                {"location": {"physicalLocation": {
                    "artifactLocation": {"uri": "data/loader.py"},
                    "region": {"startLine": 8}}}},
            ]}]}],
        }],
    }],
}

# Real transformer code. The capture layer parses THIS — the classes and
# packages in the graph are extracted, not hand-listed.
_EMBEDDING_CODE = '''
import torch
import torch.nn as nn

class TokenEmbedding(nn.Module):
    def __init__(self, vocab_size, d_model):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, d_model)

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=512):
        super().__init__()
        self.pe = torch.zeros(max_len, d_model)
'''

_ATTENTION_CODE = '''
import torch
import torch.nn as nn
from einops import rearrange

class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, n_heads):
        super().__init__()
        self.qkv = nn.Linear(d_model, d_model * 3)
        self.n_heads = n_heads
    def forward(self, x):
        qkv = rearrange(self.qkv(x), "b n (three h d) -> three b h n d", three=3)
        return qkv

class TransformerBlock(nn.Module):
    def __init__(self, d_model, n_heads):
        super().__init__()
        self.attn = MultiHeadAttention(d_model, n_heads)
        self.norm = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(nn.Linear(d_model, d_model * 4), nn.GELU())
'''

_MODEL_CODE = '''
import torch.nn as nn

class TransformerLM(nn.Module):
    def __init__(self, vocab_size, d_model, n_heads, n_layers):
        super().__init__()
        self.blocks = nn.ModuleList()
        self.head = nn.Linear(d_model, vocab_size)
'''

_TRAINER_CODE = '''
import torch
from torch.cuda.amp import autocast, GradScaler
from tqdm import tqdm

class Trainer:
    def __init__(self, model, lr=3e-4):
        self.opt = torch.optim.Adam(model.parameters(), lr=lr)
        self.scaler = GradScaler()
    def step(self, batch):
        with autocast():
            loss = self.model(batch)
        return loss
'''

# One decision rests on a poisoned source: a mirror doc that recommended an
# unsafe deprecated call. This is the edge the forget demo prunes.
_DATALOADER_CODE = '''
import numpy as np
import pickle

class UnsafeShardLoader:
    def __init__(self, path):
        self.data = pickle.load(open(path, "rb"))
        self.index = np.arange(len(self.data))
'''


def build(memory: MemoryStore) -> dict[str, Any]:
    """Record the transformer build and return the export graph."""
    rec = CodeGraphRecorder(memory)

    # sources: the user's brief, a trusted paper, and a poisoned mirror
    rec.record_source(
        "user-brief",
        "build a small transformer language model for long-context text",
        origin="user",
    )
    rec.record_source(
        "attention-paper",
        "Attention Is All You Need — multi-head attention over d_model",
        trusted=True,
    )
    rec.record_source(
        "einops-docs", "einops.rearrange for readable tensor reshaping",
        trusted=True,
    )
    rec.record_source(
        "poisoned-mirror",
        "a docs mirror recommending pickle.load on untrusted shard files",
        trusted=False,   # external, unverified
    )

    # decisions, each linked to the sources that informed it
    rec.record_decision(
        "use-pytorch", "use PyTorch as the framework",
        from_sources=["user-brief"])
    rec.record_decision(
        "transformer-arch",
        "transformer architecture for long-context handling",
        from_sources=["user-brief", "attention-paper"])
    rec.record_decision(
        "einops-reshape", "use einops.rearrange for attention reshaping",
        from_sources=["einops-docs"])
    rec.record_decision(
        "mixed-precision", "train in mixed precision with Adam",
        from_sources=["user-brief"])
    rec.record_decision(
        "pickle-shards",
        "load data shards with pickle for speed (from the mirror doc)",
        from_sources=["poisoned-mirror"])

    # code, captured by AST under each decision
    rec.record_code(_EMBEDDING_CODE, decision_id="transformer-arch")
    rec.record_code(_ATTENTION_CODE, decision_id="einops-reshape")
    rec.record_code(_MODEL_CODE, decision_id="transformer-arch")
    rec.record_code(_TRAINER_CODE, decision_id="mixed-precision")
    rec.record_code(_DATALOADER_CODE, decision_id="pickle-shards")

    # SBOM: representative pinned versions and licenses (in a real run these
    # come from the lockfile).
    rec.record_version("torch", "2.3.1", license="BSD-3-Clause")
    rec.record_version("einops", "0.7.0", license="MIT")
    rec.record_version("numpy", "1.26.4", license="BSD-3-Clause")
    rec.record_version("tqdm", "4.66.1", license="MPL-2.0")

    # Advisories from a SAMPLE feed (clearly labelled; the real OSV join is
    # the next phase). Illustrative ids, not real CVEs — they exist to
    # demonstrate the blast-radius mechanic, never to assert a real vuln.
    rec.record_cve(
        "SAMPLE-2026-0001", affects=[("torch", "2.3.1")],
        severity="high",
        summary="[sample] deserialization issue in an affected torch build",
        cwe="CWE-502", feed="sample",
    )
    rec.record_cve(
        "SAMPLE-2026-0002", affects=[("numpy", "1.26.4")],
        severity="medium",
        summary="[sample] out-of-bounds read in an affected numpy build",
        cwe="CWE-125", feed="sample",
    )

    # Reachability: ingest a real-shape scanner run whose taint flow reaches
    # the pickle.load sink. This upgrades that one finding to exploitable while
    # every other present-but-unproven sink stays merely present.
    ingest_sarif(memory, _SARIF_RUN)

    return rec.export()
