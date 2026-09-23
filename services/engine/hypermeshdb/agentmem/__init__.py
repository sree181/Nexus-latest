"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/__init__.py
Description: Agent-memory substrate: provenance-versioned, append-only
             memory hyperedges with stable ULID identity, typed derivation
             links, supersession, tombstoning with deletion certificates,
             and as-of queries. Phase 1 of the MeshAgent integration.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from ._content import ContentStore, sha256_of
from ._envelope import (
    Envelope,
    Kind,
    Memory,
    Origin,
    Rel,
    Status,
    TTLClass,
)
from ._ids import is_ulid, new_ulid
from ._registry import SqliteEntityRegistry
from .gate import (
    ContextEntry,
    GateDecision,
    GateRejection,
    RecallGate,
    WriteGate,
    scan_credentials,
    scan_instructions,
)
from .store import DERIVATION_TABLE, MEMORY_TABLE, MemoryStore
from .verbs import DeletionCertificate, Evidence, Verbs

__all__ = [
    "ContentStore",
    "ContextEntry",
    "DeletionCertificate",
    "Evidence",
    "Verbs",
    "DERIVATION_TABLE",
    "Envelope",
    "GateDecision",
    "GateRejection",
    "RecallGate",
    "WriteGate",
    "scan_credentials",
    "scan_instructions",
    "Kind",
    "MEMORY_TABLE",
    "Memory",
    "MemoryStore",
    "Origin",
    "Rel",
    "SqliteEntityRegistry",
    "Status",
    "TTLClass",
    "is_ulid",
    "new_ulid",
    "sha256_of",
]
