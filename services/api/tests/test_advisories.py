"""Resolving an imported package against the real feeds.

These are about the ways a lookup can be confidently wrong: an import name is
not a distribution name, two projects can publish the same import name, and a
project can state its license in any of three places. Every case here is
checked without touching the network -- the feed responses are supplied, since
what is under test is the reading of them, not PyPI.
"""

from __future__ import annotations

import pytest

from app import advisories


def test_an_import_name_is_looked_up_under_its_pypi_project():
    """cv2 is published as opencv-python. Looking up the import name would
    miss, and the code graph still has to join on the import name."""
    assert advisories.DISTRIBUTIONS["cv2"] == "opencv-python"
    assert advisories.DISTRIBUTIONS["yaml"] == "PyYAML"


def test_an_import_name_two_projects_publish_is_refused():
    """`Crypto` comes from pycryptodome or pycrypto, and there is an unrelated
    project called Crypto as well -- so a lookup returns a confident answer
    about the wrong software. Better to record nothing and say why."""
    out = advisories.resolve("Crypto")

    assert out.version is None
    assert out.advisories == []
    assert "more than one PyPI project" in (out.unavailable or "")
    assert "pycryptodome" in out.unavailable


def test_a_disabled_feed_reports_itself_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(advisories, "ENABLED", False)
    out = advisories.resolve("numpy")

    assert out.version is None
    assert "disabled" in (out.unavailable or "")


@pytest.mark.parametrize("info,expected", [
    # PEP 639: projects moved to an SPDX expression and left `license` empty
    ({"license_expression": "BSD-3-Clause", "license": ""}, "BSD-3-Clause"),
    ({"license": "MIT"}, "MIT"),
    # some projects paste a whole license text into the field; ignore it and
    # fall back to the classifiers
    ({"license": "x" * 200,
      "classifiers": ["License :: OSI Approved :: Apache Software License"]},
     "Apache Software License"),
    ({}, None),
])
def test_the_license_is_read_from_whichever_field_is_populated(info, expected):
    assert advisories._license(info) == expected


def test_a_cve_alias_is_preferred_over_the_osv_id():
    """OSV keys advisories by GHSA or PYSEC id and lists the CVE as an alias.
    The CVE is the name the rest of the world uses."""
    assert advisories._advisory_id(
        {"id": "GHSA-xxxx", "aliases": ["CVE-2024-1234"]}) == "CVE-2024-1234"
    assert advisories._advisory_id({"id": "PYSEC-2021-1"}) == "PYSEC-2021-1"


@pytest.mark.parametrize("vuln,expected", [
    ({"severity": [{"score": "9.8"}]}, "critical"),
    ({"severity": [{"score": "7.5"}]}, "high"),
    ({"severity": [{"score": "5.0"}]}, "medium"),
    ({"severity": [{"score": "2.0"}]}, "low"),
    # a CVSS vector string carries no usable number
    ({"severity": [{"score": "CVSS:3.1/AV:N/AC:L"}],
      "database_specific": {"severity": "MODERATE"}}, "medium"),
    ({}, "unknown"),
])
def test_severity_comes_from_the_score_then_the_label_then_nothing(vuln, expected):
    assert advisories._severity(vuln) == expected
