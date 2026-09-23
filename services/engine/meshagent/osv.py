"""
Project:     MeshAgent
File:        meshagent/osv.py
Description: The OSV.dev join. Turns the SBOM version nodes in the graph
             into real advisory hyperedges by querying the OSV database,
             replacing the sample feed with live CVE/GHSA data. The HTTP
             call is injectable, so the join is tested against a fixture
             that matches OSV's real /v1/query schema and runs live the
             moment the network allows api.osv.dev. Advisories enter as
             EXTERNAL/UNVERIFIED through the same write gate as any other
             third-party fact, so vuln intel is governed like everything
             else.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from hypermeshdb.agentmem import MemoryStore

from .codegraph import P_VERSION, CodeGraphRecorder, export_graph

OSV_BASE = "https://api.osv.dev"

# A fetch function takes (name, ecosystem, version) and returns the parsed
# OSV /v1/query JSON response. Injected in tests; defaults to a real POST.
FetchFn = Callable[[str, str, str], dict[str, Any]]


@dataclass
class Advisory:
    """One advisory parsed from an OSV vuln record."""

    id: str                       # preferred display id (CVE if available)
    osv_id: str                   # the OSV/GHSA/PYSEC id
    summary: str
    severity: str                 # critical|high|medium|low|unknown
    cwe: str | None
    aliases: list[str] = field(default_factory=list)


def _http_fetch(name: str, ecosystem: str, version: str,
                *, base: str = OSV_BASE, timeout: int = 20) -> dict[str, Any]:
    body = json.dumps({
        "package": {"ecosystem": ecosystem, "name": name},
        "version": version,
    }).encode()
    req = urllib.request.Request(
        f"{base}/v1/query", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_SEV_LABELS = {"critical", "high", "medium", "moderate", "low"}


def advisories_from_osv(resp: dict[str, Any]) -> list[Advisory]:
    """Parse an OSV /v1/query response into advisories. Prefers a CVE alias
    as the display id (that is what security teams track); takes severity
    from database_specific.severity when present (GHSA supplies it), and the
    first CWE from database_specific.cwe_ids."""
    out: list[Advisory] = []
    for v in resp.get("vulns", []) or []:
        osv_id = v.get("id", "")
        aliases = list(v.get("aliases", []) or [])
        cve = next((a for a in aliases if a.startswith("CVE-")), None)
        dbs = v.get("database_specific", {}) or {}
        sev_raw = str(dbs.get("severity", "")).lower()
        severity = sev_raw if sev_raw in _SEV_LABELS else "unknown"
        if severity == "moderate":
            severity = "medium"
        cwes = dbs.get("cwe_ids") or []
        cwe = cwes[0] if cwes else None
        summary = v.get("summary") or (v.get("details", "")[:140]) or osv_id
        out.append(Advisory(
            id=cve or osv_id, osv_id=osv_id, summary=summary,
            severity=severity, cwe=cwe, aliases=aliases,
        ))
    return out


class OSVClient:
    """Queries OSV for the advisories affecting a package version."""

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _http_fetch

    def query(self, name: str, ecosystem: str, version: str) -> list[Advisory]:
        return advisories_from_osv(self._fetch(name, ecosystem, version))


def _parse_version_node(node_id: str) -> tuple[str, str]:
    """`version:torch@2.3.1` -> ('torch', '2.3.1')."""
    body = node_id[len(P_VERSION):]
    name, _, ver = body.rpartition("@")
    return name, ver


def join_osv(
    memory: MemoryStore, *, ecosystem: str = "PyPI",
    client: OSVClient | None = None,
) -> list[str]:
    """Query OSV for every SBOM version in the graph and record the real
    advisories as hyperedges (feed='osv'). Returns the ULIDs written.

    Deduplicates against advisories already recorded for the same version,
    so a re-run adds only newly disclosed vulnerabilities."""
    client = client or OSVClient()
    rec = CodeGraphRecorder(memory)
    g = export_graph(memory)
    existing = {
        (n.get("id"),) for n in g["nodes"] if n["kind"] == "cve"
    }
    have_pairs: set[tuple[str, str]] = set()
    for e in g["sbom"]:
        if e["rel"] == "affects":
            have_pairs.add((e["source"], e["target"]))

    written: list[str] = []
    for node in g["nodes"]:
        if node["kind"] != "version":
            continue
        name, ver = _parse_version_node(node["id"])
        for adv in client.query(name, ecosystem, ver):
            cve_node = f"cve:{adv.id}"
            if (cve_node, node["id"]) in have_pairs:
                continue   # already recorded for this version
            written.append(rec.record_cve(
                adv.id, affects=[(name, ver)], severity=adv.severity,
                summary=adv.summary, cwe=adv.cwe, feed="osv",
            ))
            have_pairs.add((cve_node, node["id"]))
    return written
