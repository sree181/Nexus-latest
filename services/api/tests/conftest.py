"""Keeps the suite hermetic.

The app reads the repo's .env at import, which is what makes a real model and
real advisory feeds work in development. Tests must not inherit that: a suite
whose assertions change when a key is present is not a suite, and one that
reaches the network is both slow and billable. So every test runs as though
nothing were configured, and the tests that want a model or a feed install
their own stub explicitly.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Before any test module is imported, and so before the app package reads
# .env. Some modules build a gateway at import time, which opens stores; a
# store directory takes a writer lock, so without this the suite would fight
# the developer's running API for the real memory directory and fail to
# collect with "database is locked".
os.environ["MESHAGENT_DB_DIR"] = tempfile.mkdtemp(prefix="meshagent-tests-")


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch, tmp_path):
    # no model: runs record the reference build unless a test says otherwise
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    # memory is durable now, so each test needs its own place to put it --
    # otherwise the developer's real runs and one test's runs would show up
    # in another test's fleet. Tests about durability point two gateways at
    # a directory of their own on purpose.
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "memory"))

    # no feeds: a lookup reports itself unavailable rather than going out to
    # PyPI or OSV. Patched on the module because it is read at import time.
    try:
        from app import advisories
    except ImportError:          # engine-less environments skip those tests
        return
    monkeypatch.setattr(advisories, "ENABLED", False)
