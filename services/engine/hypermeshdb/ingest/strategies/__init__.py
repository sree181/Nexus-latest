"""
hypermeshdb.ingest.strategies
==============================
Registry of all available ingestion strategies.

Adding a new strategy
---------------------
1. Create a module in this package (e.g. ``my_strategy.py``).
2. Subclass :class:`~.base.IngestStrategy` and set ``name``, ``label``,
   ``description``, ``param_specs``, and implement ``run()``.
3. Import and register it here in ``_REGISTRY``.

Usage
-----
    from hypermeshdb.ingest.strategies import registry, get_strategy

    all_schemas = registry()          # list of JSON-serialisable dicts
    cls = get_strategy("patent_co_citation")
    result = cls().run(config, conn, db_dir, progress_cb)
"""

from __future__ import annotations

from .base import IngestStrategy, StrategyResult, ParamSpec, ProgressCallback
from .patent_co_citation       import PatentCoCitationStrategy
from .patent_assignee_portfolio import PatentAssigneePortfolioStrategy
from .patent_cpc_cluster        import PatentCPCClusterStrategy
from .patent_forward_star       import PatentForwardStarStrategy
from .mde_baseline              import MDEBaselineStrategy
from .geo.disaster              import GeoDisasterStrategy
from .mayo_clinical             import MayoClinicalStrategy

_REGISTRY: dict[str, type[IngestStrategy]] = {
    PatentCoCitationStrategy.name:        PatentCoCitationStrategy,
    PatentAssigneePortfolioStrategy.name: PatentAssigneePortfolioStrategy,
    PatentCPCClusterStrategy.name:        PatentCPCClusterStrategy,
    PatentForwardStarStrategy.name:       PatentForwardStarStrategy,
    MDEBaselineStrategy.name:             MDEBaselineStrategy,
    GeoDisasterStrategy.name:             GeoDisasterStrategy,
    MayoClinicalStrategy.name:            MayoClinicalStrategy,
}


def registry() -> list[dict]:
    """Return JSON-serialisable schema for every registered strategy."""
    return [cls().schema() for cls in _REGISTRY.values()]


def get_strategy(name: str) -> type[IngestStrategy]:
    """Return the strategy class for *name*, or raise KeyError."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy '{name}'. "
            f"Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


__all__ = [
    "IngestStrategy",
    "StrategyResult",
    "ParamSpec",
    "ProgressCallback",
    "registry",
    "get_strategy",
    "MDEBaselineStrategy",
    "MayoClinicalStrategy",
]
