"""MeshAgent API service: REST + WebSocket over the MeshAgent gateway."""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"


def _load_env() -> None:
    """Read the repo's .env into the environment, if there is one.

    Done here because a package __init__ runs before any app module, and the
    gateway decides at import time whether a model is configured. A real
    environment variable always wins: this fills gaps, it does not override.
    """
    here = Path(__file__).resolve()
    root = next(
        (parent for parent in here.parents if (parent / "package.json").is_file()),
        Path.cwd(),
    )
    env = root / ".env"
    try:
        lines = env.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # A relative memory directory means "relative to the repo", not to
        # whichever directory uvicorn happened to be started from -- getting
        # that wrong would quietly split one deployment's memory in two.
        if key == "MESHAGENT_DB_DIR" and value and not os.path.isabs(value):
            value = str(root / value)
        os.environ.setdefault(key, value)


_load_env()
