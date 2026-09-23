"""
_table.py — Unicode box-drawing table renderer and plan formatter.

Zero external dependencies.  Used by both the interactive shell and the
``hmdb query`` / ``hmdb info`` subcommands.
"""

from __future__ import annotations

from typing import Any

# Characters for the four border styles
_LIGHT = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "lv": "│", "rv": "│", "h": "─",
    "tc": "┬", "bc": "┴", "lc": "├", "rc": "┤", "x": "┼",
}

MAX_CELL_WIDTH = 40


def _cell_str(value: Any, max_width: int = MAX_CELL_WIDTH) -> str:
    """Render a single cell value as a display string."""
    if value is None:
        s = "NULL"
    elif isinstance(value, bool):
        s = "true" if value else "false"
    elif isinstance(value, float):
        s = f"{value:.6g}"
    elif isinstance(value, list):
        parts = ", ".join(str(x) for x in value)
        s = f"[{parts}]"
    else:
        s = str(value)

    if len(s) > max_width:
        s = s[: max_width - 1] + "…"
    return s


def render_table(
    columns: list[str],
    rows: list[list[Any]],
    max_col_width: int = MAX_CELL_WIDTH,
) -> str:
    """
    Render *columns* + *rows* as a Unicode box-drawing table.

    Example output::

        ┌─────────────────┬──────────────────┬───────────────┬───────────┐
        │ name            │ member_tables    │ bucket_seconds│ row_count │
        ├─────────────────┼──────────────────┼───────────────┼───────────┤
        │ CoProximity     │ [Drone, Drone]   │ 10            │ 1774      │
        └─────────────────┴──────────────────┴───────────────┴───────────┘

    Parameters
    ----------
    columns:
        Column header names.
    rows:
        Raw row data as list-of-lists (use ``QueryResult._raw_rows`` or
        ``[r.values() for r in result]``).
    max_col_width:
        Maximum display width per cell before truncation.

    Returns
    -------
    str
        Multi-line table string ready to print.
    """
    b = _LIGHT

    if not columns:
        return "(no columns)"

    # ── Compute column widths ─────────────────────────────────────────────
    col_widths = [min(len(c), max_col_width) for c in columns]
    cell_rows: list[list[str]] = []
    for row in rows:
        cells = [_cell_str(v, max_col_width) for v in row]
        for i, cell in enumerate(cells):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))
        cell_rows.append(cells)

    n = len(columns)

    def _rule(left: str, cross: str, right: str) -> str:
        segs = [b["h"] * (col_widths[i] + 2) for i in range(n)]
        return left + cross.join(segs) + right

    def _row_line(cells: list[str]) -> str:
        parts = [
            " " + (cells[i] if i < len(cells) else "").ljust(col_widths[i]) + " "
            for i in range(n)
        ]
        return b["lv"] + b["rv"].join(parts) + b["rv"]

    lines: list[str] = [
        _rule(b["tl"], b["tc"], b["tr"]),
        _row_line(columns),
        _rule(b["lc"], b["x"], b["rc"]),
    ]
    for cells in cell_rows:
        lines.append(_row_line(cells))
    lines.append(_rule(b["bl"], b["bc"], b["br"]))

    return "\n".join(lines)


def render_plan(plan: Any) -> str:
    """
    Format a :class:`~hypermeshdb.QueryPlan` as a compact one-line summary.
    Returns an empty string if *plan* is ``None``.
    """
    if plan is None:
        return ""
    return (
        f"  [{plan.strategy}  "
        f"{plan.buckets_scanned}/{plan.total_buckets} buckets  "
        f"{plan.speedup_factor:.1f}× speedup  "
        f"{plan.elapsed_us}µs]"
    )
