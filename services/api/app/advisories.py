"""Real supply-chain facts for packages a model actually imported: the
released version and license from PyPI, the advisories against it from OSV.

This module is deliberately ignorant of the engine and of the API models. It
returns what the feeds said, or nothing at all -- a lookup that fails returns
None rather than a guess, because a bill of materials that invents a version
is worse than one that admits a gap. Callers report the gap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

PYPI = "https://pypi.org/pypi/{package}/json"
OSV = "https://api.osv.dev/v1/query"

# Short: these run inside a request, and an unreachable feed must degrade
# quickly rather than hold a run open.
TIMEOUT = float(os.environ.get("MESHAGENT_FEED_TIMEOUT", "6"))

# Set to 0 to run fully offline; every lookup then reports itself unavailable.
ENABLED = os.environ.get("MESHAGENT_FEEDS", "1") not in ("0", "false", "")


@dataclass
class Advisory:
    """One advisory as the feed stated it."""
    id: str
    summary: str
    severity: str           # critical | high | medium | low | unknown
    cwe: str | None = None


# Import names that more than one PyPI project provides. An import statement
# does not say which is installed, so any choice would attribute some other
# project's version, license and advisories. `Crypto` is the cautionary case:
# there is an unrelated project published under exactly that name, so looking
# it up returns a confident answer about the wrong software.
AMBIGUOUS = {
    "Crypto": ("pycryptodome", "pycrypto"),
}

# An import name is not a distribution name. These are the common cases where
# they differ; anything else is looked up under the name as imported, and a
# miss is reported rather than guessed at.
DISTRIBUTIONS = {
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "serial": "pyserial",
    "OpenSSL": "pyOpenSSL",
    "attr": "attrs",
    "fitz": "PyMuPDF",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "pkg_resources": "setuptools",
}


@dataclass
class Resolved:
    """What the feeds knew about one package."""
    package: str                # the name as imported, which the graph joins on
    project: str = ""           # the PyPI project actually queried
    version: str | None = None
    license: str | None = None
    advisories: list[Advisory] = field(default_factory=list)
    # why this is incomplete, if it is; surfaced to the user verbatim
    unavailable: str | None = None


def _severity(vuln: dict) -> str:
    """OSV states severity two ways and sometimes neither. Prefer the CVSS
    score, fall back to the ecosystem's own label, else admit it is unknown."""
    for sev in vuln.get("severity") or []:
        score = str(sev.get("score", ""))
        # CVSS vector strings carry no number; only a bare numeric is usable
        try:
            value = float(score)
        except ValueError:
            continue
        if value >= 9.0:
            return "critical"
        if value >= 7.0:
            return "high"
        if value >= 4.0:
            return "medium"
        return "low"
    label = str((vuln.get("database_specific") or {}).get("severity", "")).lower()
    if label in ("critical", "high", "medium", "moderate", "low"):
        return "medium" if label == "moderate" else label
    return "unknown"


def _advisory_id(vuln: dict) -> str | None:
    """OSV keys advisories by GHSA or PYSEC id and lists CVE ids as aliases.
    Prefer the CVE, because that is the name the rest of the world uses."""
    for alias in vuln.get("aliases") or []:
        if str(alias).startswith("CVE-"):
            return str(alias)
    vid = vuln.get("id")
    return str(vid) if vid else None


def _cwe(vuln: dict) -> str | None:
    for key in ("cwe_ids", "cwes"):
        ids = (vuln.get("database_specific") or {}).get(key) or []
        for cid in ids:
            text = cid.get("cweId") if isinstance(cid, dict) else str(cid)
            if text and str(text).startswith("CWE-"):
                return str(text)
    return None


def _license(info: dict) -> str | None:
    """The license, from whichever field the project actually populated.

    PEP 639 moved projects to `license_expression` (an SPDX string) and left
    the older `license` free-text field empty, so a lookup that reads only one
    of them reports plenty of well-licensed packages as unstated."""
    spdx = (info.get("license_expression") or "").strip()
    if spdx:
        return spdx
    text = (info.get("license") or "").strip()
    # some projects paste an entire license text into this field
    if text and len(text) < 64:
        return text
    for c in info.get("classifiers") or []:
        if c.startswith("License :: ") and "OSI Approved ::" not in c:
            return c.rsplit(" :: ", 1)[-1]
    for c in info.get("classifiers") or []:
        if c.startswith("License :: "):
            return c.rsplit(" :: ", 1)[-1]
    return None


def resolve_at(package: str, version: str, *,
               client: httpx.Client | None = None) -> Resolved:
    """Advisories against one *specific* version.

    `resolve` answers "what is the current state of this package", which is
    the question a bill of materials asks. A gate asks a different one: an
    agent is about to install exactly this version, and the advisories that
    matter are the ones affecting it -- not the ones affecting the release
    PyPI happens to be serving today.

    Never raises, and never guesses. If OSV cannot be reached the result says
    so on `unavailable`, and the caller has to decide what to do about not
    knowing rather than being handed a clean bill of health."""
    project = DISTRIBUTIONS.get(package, package)
    out = Resolved(package=package, project=project, version=version)

    if rivals := AMBIGUOUS.get(package):
        out.project = ""
        out.unavailable = (
            f"`{package}` is provided by more than one PyPI project "
            f"({' or '.join(rivals)}), so no advisory can be attributed to it")
        return out

    if not ENABLED:
        out.unavailable = "advisory feeds are disabled for this deployment"
        return out

    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = http.post(OSV, json={
            "package": {"name": project, "ecosystem": "PyPI"},
            "version": version,
        })
        r.raise_for_status()
        for vuln in r.json().get("vulns") or []:
            if vid := _advisory_id(vuln):
                out.advisories.append(Advisory(
                    id=vid,
                    summary=(vuln.get("summary")
                             or (vuln.get("details") or "")[:160]
                             or "no summary published"),
                    severity=_severity(vuln),
                    cwe=_cwe(vuln),
                ))
    except (httpx.HTTPError, ValueError) as exc:
        out.unavailable = f"OSV was unreachable ({type(exc).__name__})"
    finally:
        if own:
            http.close()
    return out


def resolve(package: str, *, client: httpx.Client | None = None) -> Resolved:
    """The released version, license and advisories for one imported package.

    `package` is the name as imported, because that is what the code graph
    joins on; the lookup happens under its PyPI project name. Never raises: a
    feed that cannot be reached is reported on the result so the caller can
    say so instead of filling the gap in."""
    project = DISTRIBUTIONS.get(package, package)
    out = Resolved(package=package, project=project)

    rivals = AMBIGUOUS.get(package)
    if rivals:
        out.project = ""
        out.unavailable = (
            f"`{package}` is provided by more than one PyPI project "
            f"({' or '.join(rivals)}), and an import does not say which, so "
            "no version or advisory is recorded for it")
        return out

    if not ENABLED:
        out.unavailable = "advisory feeds are disabled for this deployment"
        return out

    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        try:
            r = http.get(PYPI.format(package=project))
            if r.status_code == 404:
                out.unavailable = f"PyPI has no project named {project}"
                return out
            r.raise_for_status()
            info = r.json().get("info") or {}
            out.version = (info.get("version") or "").strip() or None
            out.license = _license(info)
        except (httpx.HTTPError, ValueError) as exc:
            out.unavailable = f"PyPI was unreachable ({type(exc).__name__})"
            return out

        if not out.version:
            out.unavailable = f"PyPI did not state a version for {project}"
            return out

        try:
            r = http.post(OSV, json={
                "package": {"name": project, "ecosystem": "PyPI"},
                "version": out.version,
            })
            r.raise_for_status()
            for vuln in r.json().get("vulns") or []:
                vid = _advisory_id(vuln)
                if not vid:
                    continue
                out.advisories.append(Advisory(
                    id=vid,
                    summary=(vuln.get("summary")
                             or (vuln.get("details") or "")[:160]
                             or "no summary published"),
                    severity=_severity(vuln),
                    cwe=_cwe(vuln),
                ))
        except (httpx.HTTPError, ValueError) as exc:
            # the version is still good; only the advisory half is missing
            out.unavailable = f"OSV was unreachable ({type(exc).__name__})"
    finally:
        if own:
            http.close()
    return out
