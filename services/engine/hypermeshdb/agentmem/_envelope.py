"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/_envelope.py
Description: The provenance envelope every memory hyperedge carries, mapped
             onto native engine PROPERTIES columns. This is the schema that
             makes memory auditable: kind, origin, verification status,
             confidence, validity interval, version, TTL class, source
             locator, and the content hash that commits the graph to the
             sidecar payload.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

# The engine caps the property blob at 256 bytes (HM_MAX_PROPS_LEN).
# Budget: 8 numeric columns x 4B = 32, CSHA 4 + 64 = 68, SRC 4 + len.
# SRC is therefore capped so worst case stays comfortably under the limit.
MAX_SOURCE_LEN = 120


class Kind(IntEnum):
    """What a memory hyperedge is."""

    EPISODE = 1      # append-only record of one task run
    FACT = 2         # an assertion about the world or the user
    SKILL = 3        # a learned capability (one version)
    PREFERENCE = 4   # how the user wants things done


class Origin(IntEnum):
    """Who authored the content of a memory."""

    USER = 1       # the user said it
    AGENT = 2      # the agent concluded or produced it
    EXTERNAL = 3   # tool output, fetched page, third-party data


class Status(IntEnum):
    """Verification status. External content can never exceed UNVERIFIED
    without an explicit verification step; user statements enter as
    USER_STATED. QUARANTINED content is retrievable for audit only."""

    UNVERIFIED = 1
    VERIFIED = 2
    USER_STATED = 3
    QUARANTINED = 4


class TTLClass(IntEnum):
    """Decay class. Decay demotes confidence; it never deletes."""

    PERMANENT = 1   # episodes, audit records
    STANDARD = 2    # facts: staleness demotion applies
    VOLATILE = 3    # short-lived operational facts


class Rel(IntEnum):
    """Typed derivation relations between memory edges."""

    DERIVED_FROM = 1   # child was extracted or concluded from parents
    SUPERSEDES = 2     # child replaces parent as the current version
    TOMBSTONES = 3     # child records the redaction of parent
    SUPPORTS = 4       # parent episodes are evidence for a child skill


# DDL fragments. Two tables: memories and typed derivation links.
MEMORY_PROPS_DDL = (
    "PROPERTIES (KIND INT, ORIGIN INT, STATUS INT, CONFIDENCE FLOAT, "
    "VALID_FROM INT, VALID_TO INT, VERSION INT, TTL_CLASS INT, "
    "SRC TEXT, CSHA TEXT)"
)
DERIVATION_PROPS_DDL = "PROPERTIES (REL INT, CHILD TEXT)"


@dataclass
class Envelope:
    """The provenance envelope for one memory hyperedge."""

    kind: Kind
    origin: Origin
    status: Status
    confidence: float = 1.0
    valid_from: int = 0          # epoch seconds; 0 means "from creation"
    valid_to: int = 0            # epoch seconds; 0 means open-ended
    version: int = 1
    ttl_class: TTLClass = TTLClass.STANDARD
    source: str = ""             # locator: "user", "url:...", "tool:..."
    content_sha: str = ""        # sha256 hex of the sidecar payload

    def __post_init__(self) -> None:
        if len(self.source) > MAX_SOURCE_LEN:
            raise ValueError(
                f"source locator exceeds {MAX_SOURCE_LEN} chars; "
                "store the full locator in the content payload and pass "
                "a truncated or hashed locator here"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    def to_columns(self) -> dict[str, object]:
        """Column dict in the order MEMORY_PROPS_DDL declares."""
        return {
            "KIND": int(self.kind),
            "ORIGIN": int(self.origin),
            "STATUS": int(self.status),
            "CONFIDENCE": float(self.confidence),
            "VALID_FROM": int(self.valid_from),
            "VALID_TO": int(self.valid_to),
            "VERSION": int(self.version),
            "TTL_CLASS": int(self.ttl_class),
            "SRC": self.source,
            "CSHA": self.content_sha,
        }

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "Envelope":
        return cls(
            kind=Kind(int(row["KIND"])),
            origin=Origin(int(row["ORIGIN"])),
            status=Status(int(row["STATUS"])),
            confidence=float(row["CONFIDENCE"]),  # type: ignore[arg-type]
            valid_from=int(row["VALID_FROM"]),    # type: ignore[arg-type]
            valid_to=int(row["VALID_TO"]),        # type: ignore[arg-type]
            version=int(row["VERSION"]),          # type: ignore[arg-type]
            ttl_class=TTLClass(int(row["TTL_CLASS"])),  # type: ignore[arg-type]
            source=str(row.get("SRC", "")),
            content_sha=str(row.get("CSHA", "")),
        )


@dataclass
class Memory:
    """A memory hyperedge read back from the store."""

    ulid: str
    envelope: Envelope
    event_ts: int
    member_names: list[str] = field(default_factory=list)
    content: dict | None = None      # None when redacted or contentless
    redacted: bool = False
    superseded_by: str | None = None
    tombstoned: bool = False
