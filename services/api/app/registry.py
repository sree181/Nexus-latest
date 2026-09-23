"""The durable index of what a deployment knows.

A run's memory always survived a restart -- HyperMesh writes it to disk and
reopens it intact. What did not survive was the app's knowledge that the run
existed at all: the id, the task, how it ended. That lived in a process
dictionary, so restarting the API stranded every store on disk with nothing
pointing at it.

This module holds that bookkeeping beside the stores. Deletion certificates
live here too, because they are the evidence half of `forget`, and evidence a
restart destroys is not evidence.

Only operational bookkeeping belongs here. Beliefs stay in the graph.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import DeletionCertificate, RunStatus

INDEX = "index.json"

# Bumped when a field changes meaning. An index written by an older build is
# ignored rather than guessed at: a wrong run list is worse than an empty one.
VERSION = 1


@dataclass
class RunRecord:
    """What the app must remember about a run to find its memory again."""

    id: str
    task: str
    status: RunStatus
    created_at: int
    store: str                      # directory name under the base
    reference_build: bool = False
    model: str | None = None
    # who produced it. Attribution that a restart erased would leave the
    # analyst with findings belonging to nobody.
    owner: str | None = None
    owner_name: str | None = None
    # the external agent session this run was opened for, when a recorder
    # opened it rather than the UI. Without it here, an editor session that
    # spans a restart would open a second run and split one piece of work
    # across two.
    session: str | None = None
    agent: str | None = None    # which agent: "claude-code", "cursor", ...
    # Whether the owner above was proven rather than asserted. Kept here
    # because the coverage report names developers, and a restart that
    # forgot which names were verified would silently promote all of them.
    attributed: bool = False


@dataclass
class Registry:
    """Runs and certificates, held on disk under `base`.

    Every mutation goes through `save`, so a crash between two writes loses
    at most the write in flight."""

    base: str
    runs: dict[str, RunRecord] = field(default_factory=dict)
    certificates: list[DeletionCertificate] = field(default_factory=list)

    @property
    def path(self) -> str:
        return os.path.join(self.base, INDEX)

    def save(self) -> None:
        """Write the index atomically: a half-written index on a power cut
        would strand the stores exactly as having no index does."""
        os.makedirs(self.base, exist_ok=True)
        body = {
            "version": VERSION,
            "runs": [asdict(r) for r in self.runs.values()],
            "certificates": [c.model_dump() for c in self.certificates],
        }
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".index-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(body, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.base, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def load(base: str) -> Registry:
    """The index at `base`, or an empty one when there is nothing readable
    there. A corrupt or future-versioned index is treated as absent: the
    stores are still on disk, and inventing entries for them would put
    claims in the UI that nothing backs."""
    reg = Registry(base=base)
    try:
        with open(os.path.join(base, INDEX)) as fh:
            body: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return reg
    if body.get("version") != VERSION:
        return reg

    for raw in body.get("runs", []):
        try:
            reg.runs[raw["id"]] = RunRecord(**raw)
        except (TypeError, KeyError):
            continue            # skip the unreadable row, keep the rest
    for raw in body.get("certificates", []):
        try:
            reg.certificates.append(DeletionCertificate(**raw))
        except (TypeError, ValueError):
            continue
    return reg
