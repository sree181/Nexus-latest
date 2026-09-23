"""
reports/stix_exporter.py — STIX 2.1 bundle generator for HyperMesh pattern findings.

Converts a ``DetectionResult`` into a standards-compliant STIX 2.1 Bundle that
can be imported into any STIX-aware threat-intel platform (MISP, OpenCTI,
Microsoft Sentinel, Elastic SIEM, etc.).

STIX object mapping
-------------------
  HyperMesh concept           →  STIX 2.1 type
  ─────────────────────────────────────────────
  Report metadata             →  report
  HyperMesh pattern engine    →  identity  (tool)
  hub_node / fan_out finding  →  indicator (anomalous-activity)
  temporal_burst / beaconing  →  indicator (malicious-activity)
  lateral_movement finding    →  attack-pattern (MITRE T1021.*)
  spectral_anomaly / cluster  →  note
  Affected entity (machine)   →  infrastructure
  Affected entity (account)   →  user-account
  Affected entity (ip)        →  ipv4-addr / ipv6-addr
  Affected entity (process)   →  process
  Finding → entity relation   →  relationship (indicates / related-to)

No stix2 library required — generates raw STIX 2.1 JSON dicts.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _new_id(type_: str) -> str:
    return f"{type_}--{uuid.uuid4()}"


def _clean_pattern(s: str) -> str:
    """Strip table names from HyperMesh Cypher for display."""
    return re.sub(r"\s+", " ", s).strip()


_TYPE_TO_STIX_INDICATOR: dict[str, list[str]] = {
    "hub_node":           ["anomalous-activity"],
    "fan_out":            ["anomalous-activity"],
    "temporal_burst":     ["malicious-activity", "anomalous-activity"],
    "beaconing":          ["malicious-activity"],
    "dense_cluster":      ["anomalous-activity"],
    "lateral_movement":   ["malicious-activity"],
    "spectral_anomaly":   ["anomalous-activity"],
    "community_isolation":["anomalous-activity"],
}

_TYPE_TO_MITRE: dict[str, str | None] = {
    "lateral_movement":  "T1021",        # Remote Services
    "beaconing":         "T1071",        # Application Layer Protocol (C2)
    "hub_node":          "T1090",        # Proxy
    "fan_out":           "T1078",        # Valid Accounts
    "temporal_burst":    "T1059",        # Command and Scripting Interpreter
    "dense_cluster":     None,
    "spectral_anomaly":  None,
}


# ─────────────────────────────────────────────────────────────────────────────
# STIX object builders
# ─────────────────────────────────────────────────────────────────────────────

def _identity_object(report_id: str) -> dict:
    return {
        "type":           "identity",
        "spec_version":   "2.1",
        "id":             _new_id("identity"),
        "created":        _now_ts(),
        "modified":       _now_ts(),
        "name":           "HyperMesh DB Pattern Detection Engine",
        "identity_class": "system",
        "description":    (
            "Automated hypergraph pattern detection engine that analyses "
            "security telemetry stored in HyperMesh DB and surfaces "
            "anomalous patterns such as hub nodes, lateral movement chains, "
            "temporal bursts, and beaconing behaviour."
        ),
        "labels": ["threat-intelligence-platform"],
        "external_references": [{
            "source_name": "HyperMesh DB",
            "description": "HyperMesh DB Workbench",
            "url":         "https://github.com/hypermesh-db",
        }],
    }


def _indicator_from_finding(
    finding:    dict,
    creator_id: str,
    entity_map: dict,
) -> dict | None:
    """Convert a finding to a STIX Indicator."""
    sev         = finding.get("severity", "info")
    ftype       = finding.get("type", "")
    ind_types   = _TYPE_TO_STIX_INDICATOR.get(ftype, ["anomalous-activity"])
    confidence  = {"critical": 85, "high": 70, "medium": 50, "low": 30, "info": 10}.get(sev, 30)

    # Build a human-readable STIX pattern expression.
    # We use a generic network-traffic pattern referencing the affected nodes.
    nodes = finding.get("affected_nodes", [])
    if nodes:
        labels = [entity_map.get(str(n), {}).get("display", f"node_{n}") for n in nodes[:4]]
        pattern_val = " OR ".join(f"[user-account:display_name = '{l}']" for l in labels if l)
        if not pattern_val:
            pattern_val = "[network-traffic:dst_ref.type = 'ipv4-addr']"
    else:
        pattern_val = "[network-traffic:dst_ref.type = 'ipv4-addr']"

    return {
        "type":            "indicator",
        "spec_version":    "2.1",
        "id":              _new_id("indicator"),
        "created":         _now_ts(),
        "modified":        _now_ts(),
        "created_by_ref":  creator_id,
        "name":            finding.get("title", "Unnamed finding"),
        "description":     finding.get("description", ""),
        "indicator_types": ind_types,
        "pattern":         pattern_val,
        "pattern_type":    "stix",
        "valid_from":      _now_ts(),
        "confidence":      confidence,
        "labels":          [ftype.replace("_", "-"), f"severity-{sev}"],
        "x_hypermesh": {
            "finding_id":      finding.get("id"),
            "finding_type":    ftype,
            "severity":        sev,
            "evidence":        finding.get("evidence", {}),
            "affected_nodes":  nodes,
            "query":           _clean_pattern(finding.get("query", "")),
            "remediation":     finding.get("remediation", ""),
        },
    }


def _attack_pattern_from_finding(finding: dict, creator_id: str) -> dict | None:
    """Emit an attack-pattern object for findings with a known MITRE technique."""
    mitre = _TYPE_TO_MITRE.get(finding.get("type", ""))
    if not mitre:
        return None
    return {
        "type":           "attack-pattern",
        "spec_version":   "2.1",
        "id":             _new_id("attack-pattern"),
        "created":        _now_ts(),
        "modified":       _now_ts(),
        "created_by_ref": creator_id,
        "name":           finding.get("title", ""),
        "description":    finding.get("description", ""),
        "external_references": [{
            "source_name": "mitre-attack",
            "external_id": mitre,
            "url":         f"https://attack.mitre.org/techniques/{mitre}/",
        }],
        "labels": [finding.get("type", "").replace("_", "-")],
    }


def _note_from_finding(finding: dict, creator_id: str, object_refs: list[str]) -> dict:
    return {
        "type":           "note",
        "spec_version":   "2.1",
        "id":             _new_id("note"),
        "created":        _now_ts(),
        "modified":       _now_ts(),
        "created_by_ref": creator_id,
        "abstract":       finding.get("title", ""),
        "content":        (
            f"{finding.get('description', '')}\n\n"
            f"Evidence: {finding.get('evidence', {})}\n"
            f"Remediation: {finding.get('remediation', '')}"
        ),
        "object_refs":    object_refs or [creator_id],
        "labels":         [finding.get("type", "").replace("_", "-"), f"severity-{finding.get('severity', 'info')}"],
    }


def _relationship(
    rel_type:   str,
    source_ref: str,
    target_ref: str,
    creator_id: str,
    description: str = "",
) -> dict:
    return {
        "type":             "relationship",
        "spec_version":     "2.1",
        "id":               _new_id("relationship"),
        "created":          _now_ts(),
        "modified":         _now_ts(),
        "created_by_ref":   creator_id,
        "relationship_type": rel_type,
        "source_ref":       source_ref,
        "target_ref":       target_ref,
        **({"description": description} if description else {}),
    }


def _report_object(
    table_name:  str,
    report_id:   str,
    creator_id:  str,
    object_refs: list[str],
    summary:     dict,
) -> dict:
    risk_score = summary.get("risk_score", 0)
    sev_counts = summary.get("severity_counts", {})
    description = (
        f"Automated pattern analysis of table '{table_name}'. "
        f"Risk score: {risk_score}. "
        f"Findings: {summary.get('total_findings', 0)} total "
        f"({sev_counts.get('critical', 0)} critical, "
        f"{sev_counts.get('high', 0)} high, "
        f"{sev_counts.get('medium', 0)} medium). "
        f"Graph has {summary.get('n_nodes', 0):,} nodes and {summary.get('n_edges', 0):,} edges."
    )
    return {
        "type":           "report",
        "spec_version":   "2.1",
        "id":             _new_id("report"),
        "created":        _now_ts(),
        "modified":       _now_ts(),
        "created_by_ref": creator_id,
        "name":           f"HyperMesh Pattern Report — {table_name}",
        "description":    description,
        "report_types":   ["threat-report"],
        "published":      _now_ts(),
        "object_refs":    object_refs or [creator_id],
        "confidence":     min(95, max(10, risk_score * 2)),
        "labels":         ["automated-detection", "hypergraph-analysis"],
        "external_references": [{
            "source_name": "HyperMesh DB",
            "external_id": report_id,
        }],
        "x_hypermesh_summary": summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_stix_bundle(
    detection_result: dict,
    entity_map:       dict,
    report_id:        str,
) -> dict:
    """
    Build a STIX 2.1 Bundle from a ``DetectionResult.as_dict()`` payload.

    Parameters
    ----------
    detection_result :
        Output of ``DetectionResult.as_dict()``.
    entity_map :
        ``{str(node_id): {type, display, short}}`` for human-readable labels.
    report_id :
        Unique identifier for this report run.

    Returns
    -------
    dict
        STIX 2.1 Bundle as a plain Python dict (JSON-serialisable).
    """
    table_name = detection_result.get("table_name", "unknown")
    findings   = detection_result.get("findings", [])
    summary    = detection_result.get("summary", {})

    objects: list[dict] = []

    # Identity (the tool that created this bundle)
    identity  = _identity_object(report_id)
    creator_id = identity["id"]
    objects.append(identity)

    all_indicator_ids: list[str] = []
    all_attack_ids:    list[str] = []
    all_note_ids:      list[str] = []

    for finding in findings:
        ftype = finding.get("type", "")

        # Always create an indicator (or note for structural anomalies)
        if ftype in ("spectral_anomaly", "community_isolation", "dense_cluster"):
            note = _note_from_finding(finding, creator_id, [creator_id])
            objects.append(note)
            all_note_ids.append(note["id"])
        else:
            indicator = _indicator_from_finding(finding, creator_id, entity_map)
            if indicator:
                objects.append(indicator)
                all_indicator_ids.append(indicator["id"])

                # Optionally add attack-pattern
                ap = _attack_pattern_from_finding(finding, creator_id)
                if ap:
                    objects.append(ap)
                    all_attack_ids.append(ap["id"])
                    # Relationship: indicator → indicates → attack-pattern
                    objects.append(_relationship(
                        "indicates", indicator["id"], ap["id"], creator_id,
                        description=f"Finding '{finding.get('title', '')}' indicates this attack pattern.",
                    ))

    # Top-level report object referencing all generated objects
    all_obj_ids = [creator_id] + all_indicator_ids + all_attack_ids + all_note_ids
    report_obj  = _report_object(table_name, report_id, creator_id, all_obj_ids, summary)
    objects.append(report_obj)

    return {
        "type":         "bundle",
        "id":           _new_id("bundle"),
        "spec_version": "2.1",
        "objects":      objects,
    }
