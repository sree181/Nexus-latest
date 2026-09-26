from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.fspath(ROOT / "cli"))

from meshagent_cli.package_commands import parse_installs  # noqa: E402


def test_native_and_python_package_parser_corpus_is_python_compatible():
    fixtures = json.loads(
        (ROOT / "recorder" / "testdata" / "package_commands.json").read_text()
    )
    for fixture in fixtures:
        got = [
            {
                "name": item.name,
                "version": item.version,
                "ecosystem": item.ecosystem,
                "manager": item.manager,
                "exact": item.exact,
            }
            for item in parse_installs(fixture["command"])
        ]
        assert got == fixture["expected"], fixture["name"]


def test_python_compatibility_parser_rejects_oversized_api_fields():
    assert parse_installs("pip install " + ("a" * 257) + "==1.0.0") == []
    assert parse_installs("npm install demo@1" + ("0" * 128)) == []
