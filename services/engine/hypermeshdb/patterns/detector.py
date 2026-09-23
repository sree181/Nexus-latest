"""
patterns/detector.py — HyperMesh DB Automated Pattern Detection Engine.

Translates raw hypergraph analytics into actionable security findings by
applying statistical thresholds, graph-structural rules, and domain
heuristics drawn from threat-hunting practice.

Architecture
------------
1. ``PatternDetector(analytics, entity_map)``
   Wraps the Analytics object and (optionally) an entity-label map.

2. ``PatternDetector.detect(full_scan=False)`` → ``DetectionResult``
   Runs a configurable suite of detectors and returns a sorted list of
   ``PatternFinding`` objects, a statistical summary, and elapsed time.

Detectors
---------
Fast  (always run, suitable for 90k-row tables in < 5 s):
  hub_nodes         — nodes with degree > 2σ above mean
  temporal_burst    — burstiness coefficient B > 0.3
  dense_cluster     — density > 0.15 AND redundancy > 0.1
  fan_out           — entity appearing in > 30% of all events
  low_connectivity  — spectral_gap < 0.05

Heavy (run only when full_scan=True; can take 10–30 s on large graphs):
  lateral_movement  — s=2 adjacency chain of ≥ 3 nodes
  community_isolation — hypermodularity + spectral bisection
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class PatternFinding:
    """A single detected security-relevant pattern."""

    id:             str
    type:           str                     # hub_node | temporal_burst | dense_cluster | fan_out | lateral_movement | spectral_anomaly | community_isolation
    severity:       str                     # critical | high | medium | low | info
    title:          str
    description:    str
    evidence:       dict[str, Any]          = field(default_factory=dict)
    affected_nodes: list[int]               = field(default_factory=list)
    query:          str                     = ""
    remediation:    str                     = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id":             self.id,
            "type":           self.type,
            "severity":       self.severity,
            "title":          self.title,
            "description":    self.description,
            "evidence":       self.evidence,
            "affected_nodes": self.affected_nodes,
            "query":          self.query,
            "remediation":    self.remediation,
        }


@dataclass
class DetectionResult:
    """Container returned by ``PatternDetector.detect()``."""

    findings:   list[PatternFinding]
    summary:    dict[str, Any]
    elapsed_s:  float
    table_name: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "elapsed_s":  round(self.elapsed_s, 3),
            "findings":   [f.as_dict() for f in self.findings],
            "summary":    self.summary,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _label(nid: int, entity_map: dict[str, Any]) -> str:
    meta = entity_map.get(str(nid), {})
    return meta.get("short") or meta.get("display") or f"node_{nid}"


def _etype(nid: int, entity_map: dict[str, Any]) -> str:
    return entity_map.get(str(nid), {}).get("type", "entity")


def _sigma_threshold(values: list[float], k: float = 2.0) -> float:
    if len(values) < 2:
        return float("inf")
    mu    = statistics.mean(values)
    sigma = statistics.stdev(values)
    return mu + k * sigma


# ─────────────────────────────────────────────────────────────────────────────
# Individual detectors
# ─────────────────────────────────────────────────────────────────────────────

def _detect_hub_nodes(
    degrees: dict[int, int],
    entity_map: dict[str, Any],
    top_n: int = 8,
) -> list[PatternFinding]:
    """
    Nodes with degree > 2σ above the mean.

    Rationale: legitimate background activity distributes events broadly;
    a node appearing in an extreme fraction of events suggests it is either
    a critical pivot (attacker-controlled) or a systemic shared resource
    (lateral-movement facilitator).
    """
    if not degrees:
        return []

    values   = [float(d) for d in degrees.values()]
    mean_d   = statistics.mean(values)
    std_d    = statistics.stdev(values) if len(values) > 1 else 0.0
    thresh2  = mean_d + 2.0 * std_d
    thresh3  = mean_d + 3.0 * std_d

    findings: list[PatternFinding] = []
    for nid, deg in sorted(degrees.items(), key=lambda x: -x[1]):
        if deg <= thresh2 or len(findings) >= top_n:
            break
        label = _label(nid, entity_map)
        etype = _etype(nid, entity_map)
        pct   = sum(1 for d in values if d <= deg) / len(values) * 100

        severity = "critical" if deg >= thresh3 else "high"

        findings.append(PatternFinding(
            id          = f"hub_{nid}",
            type        = "hub_node",
            severity    = severity,
            title       = f"High-degree {etype}: {label}",
            description = (
                f"Entity appears in {deg:,} events "
                f"({pct:.0f}th percentile, {(deg / mean_d):.1f}× mean). "
                "Entities with anomalously high participation are strong candidates "
                "for C2 endpoints, lateral-movement pivots, or persistent backdoors."
            ),
            evidence    = {
                "node_id":      nid,
                "degree":       deg,
                "mean_degree":  round(mean_d, 1),
                "std_degree":   round(std_d, 1),
                "z_score":      round((deg - mean_d) / std_d, 2) if std_d else None,
                "percentile":   round(pct, 1),
            },
            affected_nodes = [nid],
            query = (
                f"MATCH HYPEREDGE (h:{'{}'}) "
                f"WHERE {nid} IN h.members "
                "RETURN h.event_ts, h.members "
                "ORDER BY h.event_ts DESC LIMIT 25"
            ),
            remediation = (
                f"Investigate all events involving {label!r}. "
                "Correlate with known-bad IOCs and review parent process trees."
            ),
        ))
    return findings


def _detect_temporal_burst(
    burstiness: float,
    entity_map: dict[str, Any],
    timestamps: "np.ndarray",
) -> list[PatternFinding]:
    """
    Burstiness coefficient B > 0.3 flags a non-Poisson, clustered event stream.

    High burstiness is characteristic of automated attack tools (beaconing,
    spray-and-pray), ransomware encryption bursts, and data-exfiltration
    upload surges.
    """
    if math.isnan(burstiness) or burstiness <= 0.3:
        return []

    severity = "critical" if burstiness > 0.7 else "high" if burstiness > 0.5 else "medium"

    # Compute burst window
    ts_sorted = np.sort(timestamps.astype(float))
    ts_valid  = ts_sorted[ts_sorted > 0]
    peak_desc = ""
    if len(ts_valid) >= 10:
        # Find densest 10-event window
        iet = np.diff(ts_valid)
        min_idx = int(np.argmin(iet))
        t_start = int(ts_valid[min_idx])
        t_end   = int(ts_valid[min(min_idx + 10, len(ts_valid) - 1)])
        peak_desc = f" Peak burst window: ts {t_start} → {t_end}."

    return [PatternFinding(
        id          = "burst_stream",
        type        = "temporal_burst",
        severity    = severity,
        title       = f"Bursty event stream (B={burstiness:.3f})",
        description = (
            f"Inter-event time distribution has burstiness coefficient B={burstiness:.3f}. "
            "B > 0 indicates events cluster in time rather than arriving uniformly. "
            "Common in automated attack tools, beaconing C2, ransomware encryption waves, "
            f"and exfiltration surges.{peak_desc}"
        ),
        evidence    = {"burstiness": round(burstiness, 4), "severity_threshold": 0.3},
        affected_nodes = [],
        query = (
            "MATCH HYPEREDGE (h) "
            "RETURN h.event_ts, COUNT(*) AS event_count "
            "ORDER BY h.event_ts LIMIT 100"
        ),
        remediation = (
            "Visualise the event timeline in Graph Explorer's Temporal view. "
            "Identify the burst window and cross-reference with known incident timelines."
        ),
    )]


def _detect_dense_cluster(
    density: float,
    redundancy: float,
    n_nodes: int,
    n_edges: int,
) -> list[PatternFinding]:
    """
    High density AND high redundancy together indicate a tight sub-graph
    where the same entities repeatedly co-appear.

    Interpretations: persistent C2 channel, lateral movement within a VLAN,
    or credential-spraying against a small set of targets.
    """
    if density <= 0.12 or redundancy <= 0.08:
        return []

    severity = "high" if (density > 0.3 and redundancy > 0.2) else "medium"

    return [PatternFinding(
        id          = "dense_cluster",
        type        = "dense_cluster",
        severity    = severity,
        title       = f"Dense co-occurrence cluster (density={density:.3f}, redundancy={redundancy:.3f})",
        description = (
            f"The hypergraph has density {density:.3f} and node co-membership redundancy "
            f"{redundancy:.3f} across {n_nodes:,} entities and {n_edges:,} events. "
            "This combination indicates a tightly-coupled group of entities that repeatedly "
            "appear in events together — consistent with persistent C2, lateral movement "
            "within a subnet, or coordinated credential abuse."
        ),
        evidence    = {
            "density":    round(density, 4),
            "redundancy": round(redundancy, 4),
            "n_nodes":    n_nodes,
            "n_edges":    n_edges,
        },
        affected_nodes = [],
        query = (
            "MATCH HYPEREDGE (h) "
            "RETURN h.members, COUNT(*) AS repeat_count "
            "ORDER BY repeat_count DESC LIMIT 10"
        ),
        remediation = (
            "Identify the recurring entity set. Check if these are the same machines "
            "or processes appearing across distinct alert types — this is textbook lateral movement."
        ),
    )]


def _detect_fan_out(
    degrees: dict[int, int],
    n_edges: int,
    entity_map: dict[str, Any],
    threshold_pct: float = 30.0,
    top_n: int = 5,
) -> list[PatternFinding]:
    """
    An entity appearing in > threshold_pct% of all events.

    Fan-out is the hallmark of a shared parent process spawning many children,
    a single C2 IP contacted from many hosts, or a common credential used
    in mass-authentication events.
    """
    if not degrees or n_edges == 0:
        return []

    findings: list[PatternFinding] = []
    for nid, deg in sorted(degrees.items(), key=lambda x: -x[1]):
        pct = deg / n_edges * 100
        if pct < threshold_pct or len(findings) >= top_n:
            break
        label = _label(nid, entity_map)
        etype = _etype(nid, entity_map)
        severity = "critical" if pct > 70 else "high" if pct > 50 else "medium"

        findings.append(PatternFinding(
            id          = f"fanout_{nid}",
            type        = "fan_out",
            severity    = severity,
            title       = f"Fan-out {etype}: {label} ({pct:.0f}% of events)",
            description = (
                f"{etype.capitalize()} {label!r} participates in {deg:,} of {n_edges:,} "
                f"events ({pct:.1f}%). Fan-out entities are shared infrastructure: "
                "a common parent process, a widely-contacted C2 endpoint, "
                "a domain controller, or an abused service account."
            ),
            evidence    = {
                "node_id":     nid,
                "degree":      deg,
                "total_edges": n_edges,
                "pct_of_events": round(pct, 1),
            },
            affected_nodes = [nid],
            query = (
                f"MATCH HYPEREDGE (h) WHERE {nid} IN h.members "
                "RETURN h.event_ts, h.members "
                "ORDER BY h.event_ts DESC LIMIT 25"
            ),
            remediation = (
                f"Pivot on {label!r}. If it is a process, review its parent and children. "
                "If it is an IP, check external threat-intel feeds."
            ),
        ))
    return findings


def _detect_spectral_anomaly(
    spectral_gap: float,
    n_nodes: int,
) -> list[PatternFinding]:
    """
    Spectral gap of the Zhou Laplacian measures graph connectivity.

    Gap = 0  → disconnected graph (multiple isolated campaigns).
    Gap < 0.05 → nearly disconnected (two loosely-coupled sub-graphs).
    """
    if n_nodes < 4:
        return []
    if spectral_gap == 0.0:
        return [PatternFinding(
            id          = "disconnected_graph",
            type        = "spectral_anomaly",
            severity    = "medium",
            title       = "Disconnected hypergraph — multiple isolated campaigns",
            description = (
                "Spectral gap = 0 means the hypergraph is disconnected: at least two groups "
                "of entities share no common events. This can indicate distinct, parallel "
                "attack campaigns or geographically separated infrastructure operating "
                "independently in the same dataset."
            ),
            evidence    = {"spectral_gap": 0.0, "n_nodes": n_nodes},
            affected_nodes = [],
            query = "MATCH HYPEREDGE (h) RETURN h.members, h.event_ts LIMIT 50",
            remediation = (
                "Use the Graph Explorer to identify each disconnected component. "
                "Investigate whether they represent separate incidents or correlated activity."
            ),
        )]
    elif spectral_gap < 0.05:
        return [PatternFinding(
            id          = "weak_connectivity",
            type        = "spectral_anomaly",
            severity    = "low",
            title       = f"Weak graph connectivity (spectral gap = {spectral_gap:.4f})",
            description = (
                f"Spectral gap {spectral_gap:.4f} indicates the hypergraph has a weak bridge "
                "connecting two loosely-coupled sub-graphs. This often appears when a small "
                "number of 'bridge' entities connect otherwise separate event clusters — "
                "a pattern seen in multi-stage intrusions and pivot chains."
            ),
            evidence    = {"spectral_gap": round(spectral_gap, 6), "n_nodes": n_nodes},
            affected_nodes = [],
            query = "MATCH HYPEREDGE (h) RETURN h.members, h.event_ts ORDER BY h.event_ts LIMIT 50",
            remediation = (
                "Find the bridging entity using the s-closeness measure in Analytics Studio. "
                "That entity is the pivot point in the attack chain."
            ),
        )]
    return []


def _detect_lateral_movement(
    s_adj: "dict[tuple[int,int], int]",
    entity_map: dict[str, Any],
    s: int = 2,
) -> list[PatternFinding]:
    """
    A connected component of ≥ 3 nodes in the s=2 adjacency graph.

    Two nodes are s-adjacent when they share ≥ s events.  A chain of such
    nodes is a traversal path — the hallmark of lateral movement where an
    adversary pivots through shared processes or credentials.
    """
    if not s_adj:
        return []

    # Build undirected adjacency
    adj: dict[int, set[int]] = {}
    for (u, v) in s_adj:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)

    # Find connected components via BFS
    visited:    set[int]           = set()
    components: list[list[int]]    = []

    for start in adj:
        if start in visited:
            continue
        comp:  list[int] = []
        stack: list[int] = [start]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            comp.append(n)
            stack.extend(adj.get(n, set()) - visited)
        components.append(comp)

    if not components:
        return []
    largest = max(components, key=len)
    if len(largest) < 3:
        return []

    labels = [_label(n, entity_map) for n in largest[:8]]
    etypes = list({_etype(n, entity_map) for n in largest})

    severity = "critical" if len(largest) >= 6 else "high" if len(largest) >= 4 else "medium"

    return [PatternFinding(
        id          = f"lateral_chain_s{s}",
        type        = "lateral_movement",
        severity    = severity,
        title       = f"Lateral movement chain: {len(largest)} entities share ≥{s} common events",
        description = (
            f"A cluster of {len(largest)} entities forms a tight s={s} adjacency graph — "
            "each pair shares at least {s} events. "
            f"Entity types involved: {', '.join(etypes)}. "
            "This pattern is consistent with lateral movement, pass-the-hash pivoting, "
            "or a shared C2 channel contacting multiple endpoints."
        ),
        evidence    = {
            "s":              s,
            "chain_length":   len(largest),
            "n_components":   len(components),
            "node_ids":       largest[:15],
            "sample_labels":  labels,
        },
        affected_nodes = largest[:20],
        query = (
            f"MATCH HYPEREDGE (h) WHERE {largest[0]} IN h.members "
            "RETURN h.event_ts, h.members "
            "ORDER BY h.event_ts DESC LIMIT 25"
        ),
        remediation = (
            "Map the s-adjacency chain in Graph Explorer. Follow the chain from "
            "the lowest-entropy node (most-regular activity) as the likely origin. "
            "Cross-reference each pivot entity with authentication logs."
        ),
    )]


def _detect_low_entropy_nodes(
    entropy: dict[int, float],
    degrees: dict[int, int],
    entity_map: dict[str, Any],
    top_n: int = 5,
) -> list[PatternFinding]:
    """
    Active nodes (degree > 5) with very low temporal entropy.

    Low entropy means activity is concentrated in a small number of
    time-windows — typical of beaconing (perfectly regular heartbeat) or
    a single large burst.  Random legitimate user activity distributes
    much more evenly across time.
    """
    if not entropy or not degrees:
        return []

    # Only consider nodes with meaningful activity
    active = {nid: e for nid, e in entropy.items() if degrees.get(nid, 0) >= 5}
    if not active:
        return []

    values    = list(active.values())
    threshold = max(0.0, _sigma_threshold(values, k=-2.0))  # below mean - 2σ

    findings: list[PatternFinding] = []
    for nid, ent in sorted(active.items(), key=lambda x: x[1]):
        if ent > threshold or len(findings) >= top_n:
            break
        label = _label(nid, entity_map)
        etype = _etype(nid, entity_map)
        deg   = degrees.get(nid, 0)
        severity = "high" if ent < 0.5 else "medium"

        findings.append(PatternFinding(
            id          = f"beacon_{nid}",
            type        = "beaconing",
            severity    = severity,
            title       = f"Possible beaconing — {etype}: {label} (entropy={ent:.3f} bits)",
            description = (
                f"{etype.capitalize()} {label!r} participates in {deg:,} events but has "
                f"temporal entropy of only {ent:.3f} bits. Low entropy means activity is "
                "concentrated in very few time windows — a telltale sign of automated "
                "beaconing (C2 heartbeat) or a scheduled malicious task."
            ),
            evidence    = {
                "node_id":  nid,
                "entropy":  round(ent, 4),
                "degree":   deg,
            },
            affected_nodes = [nid],
            query = (
                f"MATCH HYPEREDGE (h) WHERE {nid} IN h.members "
                "RETURN h.event_ts, h.members "
                "ORDER BY h.event_ts ASC LIMIT 50"
            ),
            remediation = (
                f"Plot the event timeline for {label!r} and look for regular intervals. "
                "Check parent process lineage and network destination for beaconing signatures."
            ),
        ))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# PatternDetector
# ─────────────────────────────────────────────────────────────────────────────

class PatternDetector:
    """
    Automated security pattern detector for HyperMesh DB hypergraphs.

    Parameters
    ----------
    analytics :
        An ``Analytics`` instance (from ``db.analytics(table)``).
    entity_map :
        Optional dict ``{str(node_id): {type, display, short}}`` for
        human-readable labels in findings.
    table_name :
        Name of the source table (for reporting).
    """

    def __init__(
        self,
        analytics: Any,                      # hypermeshdb._analytics.Analytics
        entity_map: dict[str, Any] | None = None,
        table_name: str = "unknown",
    ) -> None:
        self._an         = analytics
        self._em         = entity_map or {}
        self._table_name = table_name

    # ── Public API ────────────────────────────────────────────────────────

    def detect(self, full_scan: bool = False) -> DetectionResult:
        """
        Run all detectors and return a :class:`DetectionResult`.

        Parameters
        ----------
        full_scan :
            When *True*, run heavy detectors (lateral movement via s-adjacency,
            temporal entropy per node).  Adds ~10–30 s on large tables.
        """
        t0       = time.perf_counter()
        hg       = self._an.hypergraph
        findings: list[PatternFinding] = []

        # ── Always-run (fast) measures ────────────────────────────────────
        degrees     = self._an.node_degree()          # {int: int}
        density     = self._an.density()              # float
        redundancy  = self._an.redundancy()           # float
        burstiness  = self._an.burstiness()           # float
        spectral_gap = 0.0

        # Spectral gap — skip for very large graphs (>8 k nodes) to avoid OOM
        if hg.n_nodes <= 8_000:
            try:
                spectral_gap = self._an.spectral_gap()
            except Exception:
                spectral_gap = 0.0

        findings += _detect_hub_nodes(degrees, self._em)
        findings += _detect_temporal_burst(burstiness, self._em, hg.timestamps)
        findings += _detect_dense_cluster(density, redundancy, hg.n_nodes, hg.n_edges)
        findings += _detect_fan_out(degrees, hg.n_edges, self._em)
        findings += _detect_spectral_anomaly(spectral_gap, hg.n_nodes)

        # ── Heavy measures (opt-in) ───────────────────────────────────────
        if full_scan:
            # Lateral movement via s=2 adjacency — skip for very large graphs
            if hg.n_nodes <= 15_000:
                try:
                    s_adj = self._an.s_adjacency(s=2)
                    findings += _detect_lateral_movement(s_adj, self._em, s=2)
                except Exception:
                    pass

            # Temporal entropy per node — skip for very large graphs
            if hg.n_nodes <= 20_000:
                try:
                    entropy = self._an.temporal_degree_entropy()
                    findings += _detect_low_entropy_nodes(entropy, degrees, self._em)
                except Exception:
                    pass

        # ── Sort by severity, then by evidence score ──────────────────────
        findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.id))

        elapsed = time.perf_counter() - t0

        summary = self._build_summary(
            degrees     = degrees,
            density     = density,
            redundancy  = redundancy,
            burstiness  = burstiness,
            spectral_gap= spectral_gap,
            findings    = findings,
        )

        return DetectionResult(
            findings   = findings,
            summary    = summary,
            elapsed_s  = elapsed,
            table_name = self._table_name,
        )

    # ── Internals ────────────────────────────────────────────────────────

    def _build_summary(
        self,
        degrees:      dict[int, int],
        density:      float,
        redundancy:   float,
        burstiness:   float,
        spectral_gap: float,
        findings:     list[PatternFinding],
    ) -> dict[str, Any]:
        hg = self._an.hypergraph

        sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1

        deg_values = list(degrees.values()) if degrees else [0]
        risk_score = (
            sev_counts["critical"] * 10 +
            sev_counts["high"]     *  5 +
            sev_counts["medium"]   *  2 +
            sev_counts["low"]      *  1
        )

        return {
            "n_nodes":        hg.n_nodes,
            "n_edges":        hg.n_edges,
            "density":        round(density, 4),
            "redundancy":     round(redundancy, 4),
            "burstiness":     round(burstiness, 4) if not math.isnan(burstiness) else None,
            "spectral_gap":   round(spectral_gap, 4),
            "mean_degree":    round(statistics.mean(deg_values), 2) if deg_values else 0,
            "max_degree":     max(deg_values) if deg_values else 0,
            "total_findings": len(findings),
            "severity_counts": sev_counts,
            "risk_score":     risk_score,
        }
