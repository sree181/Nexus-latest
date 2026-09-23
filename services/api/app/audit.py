"""What was done to governed memory, by whom.

The memory graph records what the agent believed. This records what *people*
did to that record: who deleted evidence, who started a run, who was refused
something that was not theirs. Those are different questions, and the second
one is the auditor's.

Append-only, and hash-chained: every entry commits to the digest of the one
before it, so removing or editing a line breaks the chain from that point on
and `verify` will say where. That does not make the log unforgeable -- anyone
who can rewrite the file can recompute the whole chain -- but it does mean
a deletion cannot be quietly snipped out of the middle, which is the realistic
insider case. Signing the chain head with a key the API does not hold is the
next step, and is not done yet.

Deliberately not in the hypergraph. Memory is the thing under scrutiny; a
record of who tampered with it should not live somewhere the same `forget`
can reach.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

LOG = "audit.jsonl"

GENESIS = "0" * 64


def strict_enabled() -> bool:
    """Whether audit persistence failures must reject the request.

    Keep the accepted values explicit so an accidental value such as ``0`` or
    ``false`` cannot enable strict mode by merely being non-empty.
    """
    return os.environ.get("MESHAGENT_STRICT_AUDIT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass
class Entry:
    """One thing someone did."""

    at: int                     # epoch seconds
    actor: str                  # the identity provider's subject
    actor_name: str
    role: str
    # False when no provider was configured and the actor was merely
    # asserted. An audit line has to carry how much it can be relied on.
    verified: bool
    action: str                 # e.g. run.forget, run.create, access.denied
    target: str                 # what it was done to
    detail: str = ""
    prev: str = GENESIS         # digest of the preceding entry
    digest: str = ""            # over this entry's contents and `prev`

    def compute(self) -> str:
        body = {k: v for k, v in asdict(self).items() if k != "digest"}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Log:
    """The append-only record, held as JSON lines under `base`."""

    base: str
    _head: str | None = field(default=None, repr=False)

    @property
    def path(self) -> str:
        return os.path.join(self.base, LOG)

    def head(self) -> str:
        """Digest of the last entry, read off disk the first time so a
        restart continues the chain rather than starting a new one."""
        if self._head is None:
            entries = self.read()
            self._head = entries[-1].digest if entries else GENESIS
        return self._head

    def record(self, *, actor: str, actor_name: str, role: str,
               verified: bool, action: str, target: str,
               detail: str = "") -> Entry:
        entry = Entry(
            at=int(time.time()), actor=actor, actor_name=actor_name,
            role=role, verified=verified, action=action, target=target,
            detail=detail, prev=self.head(),
        )
        entry.digest = entry.compute()
        os.makedirs(self.base, exist_ok=True)
        # append and flush: a line that is not on disk when the process dies
        # is a line that did not happen as far as the auditor is concerned
        with open(self.path, "a") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._head = entry.digest
        return entry

    def read(self, limit: int | None = None) -> list[Entry]:
        """Entries oldest first. A line that will not parse is skipped, and
        the chain check downstream will notice the gap."""
        try:
            with open(self.path) as fh:
                lines = fh.readlines()
        except OSError:
            return []
        out: list[Entry] = []
        for line in lines:
            try:
                raw: dict[str, Any] = json.loads(line)
                out.append(Entry(**raw))
            except (json.JSONDecodeError, TypeError):
                continue
        return out[-limit:] if limit else out

    def verify(self) -> int | None:
        """Index of the first entry whose chain does not hold, or None when
        the log is intact. This is the question an auditor actually asks."""
        expected = GENESIS
        for i, entry in enumerate(self.read()):
            if entry.prev != expected or entry.digest != entry.compute():
                return i
            expected = entry.digest
        return None
