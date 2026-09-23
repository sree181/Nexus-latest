"""
reports/html_renderer.py — Self-contained HTML report for HyperMesh pattern findings.

Generates a fully-styled, print-to-PDF-ready HTML document that includes:
  • Executive summary with risk score and severity breakdown
  • Per-finding cards with evidence tables, investigation queries, and remediation
  • Graph statistics panel
  • Affected entity table
  • Footer with generation metadata

No external dependencies — all CSS is inline.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Severity palette (hex colours matching the UI)
# ─────────────────────────────────────────────────────────────────────────────

_SEV_COLOUR = {
    "critical": ("#dc2626", "#fef2f2", "#fecaca"),   # red   border, bg, light
    "high":     ("#ea580c", "#fff7ed", "#fed7aa"),   # orange
    "medium":   ("#ca8a04", "#fefce8", "#fef08a"),   # yellow
    "low":      ("#2563eb", "#eff6ff", "#bfdbfe"),   # blue
    "info":     ("#64748b", "#f8fafc", "#e2e8f0"),   # slate
}

_SEV_ICON = {
    "critical": "&#9888;",   # ⚠
    "high":     "&#9673;",   # ◉
    "medium":   "&#9675;",   # ○
    "low":      "&#9432;",   # ℹ
    "info":     "&#9432;",
}

_TYPE_LABEL = {
    "hub_node":            "Hub Node",
    "temporal_burst":      "Temporal Burst",
    "dense_cluster":       "Dense Cluster",
    "fan_out":             "Fan-Out Entity",
    "lateral_movement":    "Lateral Movement",
    "spectral_anomaly":    "Spectral Anomaly",
    "beaconing":           "Beaconing",
    "community_isolation": "Community Isolation",
}


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px; color: #1e293b; background: #f8fafc;
  line-height: 1.5;
}
.page { max-width: 960px; margin: 0 auto; padding: 32px 24px; }

/* ── Header ── */
.report-header {
  background: linear-gradient(135deg, #1e1b4b 0%, #312e81 60%, #4338ca 100%);
  color: #fff; border-radius: 12px; padding: 32px 36px; margin-bottom: 28px;
}
.report-header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }
.report-header .subtitle { font-size: 13px; color: #c7d2fe; margin-top: 4px; }
.report-header .meta { margin-top: 20px; display: flex; gap: 28px; flex-wrap: wrap; }
.report-header .meta-item { display: flex; flex-direction: column; }
.report-header .meta-item .label { font-size: 10px; text-transform: uppercase;
  letter-spacing: .8px; color: #a5b4fc; }
.report-header .meta-item .value { font-size: 15px; font-weight: 600; color: #fff; }

/* ── Section ── */
.section { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 20px 24px; margin-bottom: 20px; }
.section-title { font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .8px; color: #64748b; margin-bottom: 16px;
  display: flex; align-items: center; gap: 8px; }
.section-title::after { content: ''; flex: 1; height: 1px; background: #e2e8f0; }

/* ── Risk meter ── */
.risk-row { display: flex; align-items: center; gap: 16px; }
.risk-bar-wrap { flex: 1; height: 10px; background: #e2e8f0; border-radius: 99px; overflow: hidden; }
.risk-bar { height: 100%; border-radius: 99px; transition: width .4s; }
.risk-label { font-size: 14px; font-weight: 700; min-width: 120px; }
.risk-critical { color: #dc2626; }
.risk-high     { color: #ea580c; }
.risk-medium   { color: #ca8a04; }
.risk-ok       { color: #16a34a; }

/* ── KPI grid ── */
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 12px; }
.kpi-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 12px 14px; }
.kpi-card .kpi-label { font-size: 10px; color: #94a3b8; text-transform: uppercase;
  letter-spacing: .6px; }
.kpi-card .kpi-value { font-size: 18px; font-weight: 700; color: #1e293b; margin-top: 2px; }

/* ── Severity pills ── */
.sev-grid { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; }
.sev-pill { display: flex; align-items: center; gap: 6px; padding: 6px 14px;
  border-radius: 99px; font-size: 12px; font-weight: 600; }

/* ── Finding card ── */
.finding { border-radius: 8px; margin-bottom: 12px; overflow: hidden;
  border: 1px solid #e2e8f0; }
.finding-header { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; }
.finding-badge { display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 99px; font-size: 11px; font-weight: 700;
  white-space: nowrap; flex-shrink: 0; margin-top: 1px; }
.finding-type { font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .6px; color: #94a3b8; background: #f1f5f9; padding: 2px 8px;
  border-radius: 4px; display: inline-block; margin-bottom: 4px; }
.finding-title { font-size: 13px; font-weight: 600; color: #1e293b; }
.finding-desc { font-size: 12px; color: #475569; margin-top: 4px; line-height: 1.55; }
.finding-body { padding: 0 16px 14px 16px; border-top: 1px solid #f1f5f9; padding-top: 12px; }

/* ── Evidence table ── */
.evidence-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; }
.evidence-table th { background: #f8fafc; color: #64748b; font-weight: 600;
  text-align: left; padding: 5px 10px; border: 1px solid #e2e8f0; }
.evidence-table td { padding: 5px 10px; border: 1px solid #e2e8f0; color: #334155; }
.evidence-table tr:nth-child(even) td { background: #f8fafc; }

/* ── Query block ── */
.query-block { background: #0f172a; color: #86efac; font-family: monospace;
  font-size: 11px; padding: 10px 14px; border-radius: 6px; margin-top: 10px;
  white-space: pre-wrap; word-break: break-all; }

/* ── Remediation ── */
.remediation { background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px;
  padding: 10px 14px; margin-top: 10px; font-size: 12px; color: #92400e; }
.remediation::before { content: "⚑ Remediation: "; font-weight: 700; }

/* ── Entity table ── */
.entity-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.entity-table th { background: #1e293b; color: #e2e8f0; text-align: left;
  padding: 8px 12px; font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .5px; }
.entity-table td { padding: 7px 12px; border-bottom: 1px solid #f1f5f9; }
.entity-table tr:hover td { background: #f8fafc; }
.etype-badge { display: inline-block; padding: 1px 7px; border-radius: 4px;
  font-size: 10px; font-weight: 600; text-transform: uppercase; }
.etype-machine  { background: #dbeafe; color: #1d4ed8; }
.etype-process  { background: #d1fae5; color: #065f46; }
.etype-account  { background: #ede9fe; color: #6d28d9; }
.etype-ip       { background: #fce7f3; color: #9d174d; }
.etype-generic  { background: #f1f5f9; color: #475569; }

/* ── Footer ── */
.report-footer { text-align: center; color: #94a3b8; font-size: 11px;
  margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; }

/* ── Print ── */
@media print {
  body { background: #fff; }
  .page { padding: 0; }
  .finding { page-break-inside: avoid; }
  .section { border: 1px solid #ccc; box-shadow: none; }
  .report-header { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _h(s: Any) -> str:
    """HTML-escape a value."""
    return html.escape(str(s))


def _sev_style(sev: str) -> tuple[str, str, str]:
    """Return (border_colour, bg_colour, light_colour) for a severity."""
    return _SEV_COLOUR.get(sev, _SEV_COLOUR["info"])


def _risk_class(score: int) -> str:
    if score >= 30: return "risk-critical"
    if score >= 15: return "risk-high"
    if score >= 5:  return "risk-medium"
    return "risk-ok"


def _risk_label(score: int) -> str:
    if score >= 30: return "Critical Risk"
    if score >= 15: return "High Risk"
    if score >= 5:  return "Medium Risk"
    return "Low Risk"


def _risk_bar_colour(score: int) -> str:
    if score >= 30: return "#dc2626"
    if score >= 15: return "#ea580c"
    if score >= 5:  return "#ca8a04"
    return "#16a34a"


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _render_header(table_name: str, generated_at: str, elapsed_s: float, n_findings: int, risk_score: int) -> str:
    risk_lbl = _risk_label(risk_score)
    return f"""
<div class="report-header">
  <h1>HyperMesh DB — Security Pattern Report</h1>
  <div class="subtitle">Automated threat-pattern analysis for table <strong>{_h(table_name)}</strong></div>
  <div class="meta">
    <div class="meta-item"><span class="label">Generated</span><span class="value">{_h(generated_at)}</span></div>
    <div class="meta-item"><span class="label">Table</span><span class="value">{_h(table_name)}</span></div>
    <div class="meta-item"><span class="label">Findings</span><span class="value">{n_findings}</span></div>
    <div class="meta-item"><span class="label">Risk Level</span><span class="value">{_h(risk_lbl)}</span></div>
    <div class="meta-item"><span class="label">Analysis Time</span><span class="value">{elapsed_s:.2f} s</span></div>
  </div>
</div>
"""


def _render_executive_summary(summary: dict) -> str:
    score     = summary.get("risk_score", 0)
    bar_pct   = min(100, int(score / 50 * 100))
    bar_clr   = _risk_bar_colour(score)
    risk_cls  = _risk_class(score)
    risk_lbl  = _risk_label(score)
    sev_counts = summary.get("severity_counts", {})

    sev_pills = ""
    for sev in ("critical", "high", "medium", "low", "info"):
        cnt = sev_counts.get(sev, 0)
        if cnt == 0:
            continue
        clr, bg, _ = _sev_style(sev)
        sev_pills += f"""
<div class="sev-pill" style="background:{bg};color:{clr};border:1px solid {_};">
  {_SEV_ICON.get(sev, "")} {sev.capitalize()}: <strong>{cnt}</strong>
</div>""".replace("_", _)

    burstiness = summary.get("burstiness")
    burst_str  = f"{burstiness:.4f}" if burstiness is not None and not math.isnan(burstiness) else "—"

    kpis = [
        ("Nodes",       f"{summary.get('n_nodes', 0):,}"),
        ("Events",      f"{summary.get('n_edges', 0):,}"),
        ("Density",     f"{summary.get('density', 0):.4f}"),
        ("Redundancy",  f"{summary.get('redundancy', 0):.4f}"),
        ("Burstiness",  burst_str),
        ("Spectral Gap",f"{summary.get('spectral_gap', 0):.4f}"),
        ("Mean Degree", f"{summary.get('mean_degree', 0):.1f}"),
        ("Max Degree",  f"{summary.get('max_degree', 0):,}"),
    ]
    kpi_html = "".join(
        f'<div class="kpi-card"><div class="kpi-label">{_h(k)}</div><div class="kpi-value">{_h(v)}</div></div>'
        for k, v in kpis
    )

    return f"""
<div class="section">
  <div class="section-title">Executive Summary</div>

  <div class="risk-row" style="margin-bottom:16px;">
    <div class="risk-bar-wrap">
      <div class="risk-bar" style="width:{bar_pct}%;background:{bar_clr};"></div>
    </div>
    <div class="risk-label {risk_cls}">{_h(risk_lbl)} (Score: {score})</div>
  </div>

  <div class="sev-grid">{sev_pills}</div>

  <div class="section-title" style="margin-top:20px;">Graph Statistics</div>
  <div class="kpi-grid">{kpi_html}</div>
</div>
"""


def _render_finding(f: dict) -> str:
    sev  = f.get("severity", "info")
    clr, bg, light = _sev_style(sev)
    icon = _SEV_ICON.get(sev, "")
    type_label = _TYPE_LABEL.get(f.get("type", ""), f.get("type", ""))

    # Evidence rows
    evidence  = f.get("evidence", {})
    ev_rows   = ""
    for k, v in evidence.items():
        if isinstance(v, list):
            v_str = ", ".join(str(x) for x in v[:8])
            if len(v) > 8:
                v_str += f" … +{len(v) - 8}"
        else:
            v_str = str(v)
        ev_rows += f"<tr><td><strong>{_h(k.replace('_', ' '))}</strong></td><td>{_h(v_str)}</td></tr>"

    ev_table = f"""
<table class="evidence-table">
  <thead><tr><th>Metric</th><th>Value</th></tr></thead>
  <tbody>{ev_rows}</tbody>
</table>""" if ev_rows else ""

    # Query
    query_html = ""
    if f.get("query"):
        query_html = f'<div class="query-block">{_h(f["query"])}</div>'

    # Remediation
    rem_html = ""
    if f.get("remediation"):
        rem_html = f'<div class="remediation">{_h(f["remediation"])}</div>'

    # Affected nodes (first 6)
    nodes = f.get("affected_nodes", [])
    nodes_html = ""
    if nodes:
        nodes_html = f'<div style="margin-top:8px;font-size:11px;color:#64748b;">Affected node IDs: <code>{", ".join(str(n) for n in nodes[:8])}{" …" if len(nodes) > 8 else ""}</code></div>'

    return f"""
<div class="finding" style="border-left:4px solid {clr};">
  <div class="finding-header" style="background:{bg};">
    <div class="finding-badge" style="background:{light};color:{clr};border:1px solid {clr}20;">
      {icon} {_h(sev.capitalize())}
    </div>
    <div>
      <div class="finding-type">{_h(type_label)}</div>
      <div class="finding-title">{_h(f.get("title", ""))}</div>
      <div class="finding-desc">{_h(f.get("description", ""))}</div>
    </div>
  </div>
  <div class="finding-body">
    {ev_table}
    {nodes_html}
    {query_html}
    {rem_html}
  </div>
</div>
"""


def _render_entity_table(findings: list[dict], entity_map: dict) -> str:
    # Collect all unique affected node IDs and their labels
    seen: dict[int, dict] = {}
    for f in findings:
        for nid in f.get("affected_nodes", []):
            if nid not in seen:
                meta = entity_map.get(str(nid), {})
                seen[nid] = {
                    "id":      nid,
                    "display": meta.get("display") or meta.get("short") or f"node_{nid}",
                    "type":    meta.get("type", "generic"),
                    "sev":     f.get("severity", "info"),
                }

    if not seen:
        return ""

    rows = ""
    for nid, info in sorted(seen.items()):
        etype = info["type"]
        rows += f"""<tr>
  <td><code>{_h(str(nid))}</code></td>
  <td>{_h(info["display"])}</td>
  <td><span class="etype-badge etype-{_h(etype)}">{_h(etype)}</span></td>
  <td><span class="finding-badge" style="font-size:10px;background:{_sev_style(info["sev"])[2]};color:{_sev_style(info["sev"])[0]};border:none;">{_h(info["sev"].capitalize())}</span></td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Affected Entities ({len(seen)})</div>
  <table class="entity-table">
    <thead><tr><th>Node ID</th><th>Label</th><th>Type</th><th>Highest Severity</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
"""


def _render_findings_section(findings: list[dict]) -> str:
    if not findings:
        return """<div class="section"><p style="color:#64748b;text-align:center;padding:24px;">
No patterns detected — the hypergraph appears healthy.</p></div>"""

    cards = "".join(_render_finding(f) for f in findings)
    return f"""
<div class="section">
  <div class="section-title">Findings ({len(findings)})</div>
  {cards}
</div>
"""


def _render_footer(report_id: str, generated_at: str) -> str:
    return f"""
<div class="report-footer">
  <p>Generated by <strong>HyperMesh DB Pattern Detection Engine</strong> &bull;
  Report ID: <code>{_h(report_id)}</code> &bull; {_h(generated_at)}</p>
  <p style="margin-top:4px;">This report is confidential. Handle in accordance with your organisation's data-classification policy.</p>
</div>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def render_html_report(
    detection_result: dict,
    entity_map:       dict,
    report_id:        str,
) -> str:
    """
    Render a self-contained HTML report from a ``DetectionResult.as_dict()`` payload.

    Parameters
    ----------
    detection_result :
        Output of ``DetectionResult.as_dict()``.
    entity_map :
        ``{str(node_id): {type, display, short}}`` for human-readable labels.
    report_id :
        A unique identifier string for this report (UUID recommended).

    Returns
    -------
    str
        Complete HTML document (self-contained, no external deps).
    """
    table_name   = detection_result.get("table_name", "unknown")
    elapsed_s    = float(detection_result.get("elapsed_s", 0))
    findings     = detection_result.get("findings", [])
    summary      = detection_result.get("summary", {})
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = (
        _render_header(table_name, generated_at, elapsed_s, len(findings), summary.get("risk_score", 0))
        + _render_executive_summary(summary)
        + _render_findings_section(findings)
        + _render_entity_table(findings, entity_map)
        + _render_footer(report_id, generated_at)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HyperMesh Security Report — {_h(table_name)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
{body}
</div>
</body>
</html>"""
