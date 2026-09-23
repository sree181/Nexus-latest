"""Where this deployment keeps what it must not lose.

Separate from engine_seed so that modules which must load without the
HyperMesh engine -- the route layer in sample mode, the audit log -- can ask
the same question and get the same answer. Two different answers would split
one deployment's records across two directories.
"""

from __future__ import annotations

import os
import tempfile

# The throwaway base used when none is configured, minted once per process so
# that a single run's stores at least sit together. Never reused across
# processes: that is what MESHAGENT_DB_DIR is for.
_EPHEMERAL: str | None = None


def base_dir() -> str:
    """The directory holding governed memory and the records about it.

    `MESHAGENT_DB_DIR` makes it durable, which is what any deployment
    somebody else uses needs. Unset, everything goes to a temporary
    directory and dies with the process -- fine for a test, dishonest for
    anything audited."""
    configured = os.environ.get("MESHAGENT_DB_DIR")
    if configured:
        return configured
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = tempfile.mkdtemp(prefix="meshagent-")
    return _EPHEMERAL


def durable() -> bool:
    """True when what is written here survives this process."""
    return bool(os.environ.get("MESHAGENT_DB_DIR"))
