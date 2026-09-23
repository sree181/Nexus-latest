"""
_shell.py — Interactive Cypher shell for HyperMesh DB.

Launched by ``hmdb shell <db_dir>``.

Features
--------
- readline line-editing and persistent history (~/.hmdb_history)
- Unicode table output with query-plan summary on every result
- Special dot-commands: .tables, .info, .compact, .help, .quit
- Multi-token queries are accepted on a single line; no multi-line mode needed
  because HyperMesh DB queries are intentionally concise
"""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._connection import Connection

_BANNER = """\
╔══════════════════════════════════════════════╗
║   HyperMesh DB Shell  ·  version {version:<13} ║
╚══════════════════════════════════════════════╝
Type  HELP  for usage,  QUIT  to exit.
"""

_HELP_TEXT = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HyperMesh DB Shell — quick reference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Query statements
  CALL show_hyperedge_tables() RETURN *
      List all defined hyperedge tables.

  MATCH HYPEREDGE (he:<Table>)
    WHERE he.event_ts >= T1 AND he.event_ts <= T2 RETURN *
      Temporal range query (TPI bucket pushdown).

  CALL GET_HYPEREDGES_BY_TIME_RANGE('<Table>', T1, T2) RETURN *
      Alternative range syntax.

  MATCH HYPEREDGE (he:<Table>) WHERE N IN he.members RETURN *
      Forward Member Index point lookup for node N.

  MATCH HYPEREDGE (he:<Table>) RETURN *
      Full table scan.

DDL statements
  CREATE HYPEREDGE TABLE <Name> (<M1>, <M2>) [BUCKET_SECONDS n]
  DROP HYPEREDGE TABLE <Name>

Special commands
  HELP  or  ?      Show this message
  QUIT  or  EXIT   Exit the shell
  .tables          List hyperedge tables (shortcut)
  .info            Show database statistics
  .compact         Compact WAL into TPI+FMI index
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _setup_readline() -> None:
    """Enable readline line-editing and persistent command history."""
    try:
        import readline
        import atexit
        history_file = os.path.expanduser("~/.hmdb_history")
        try:
            readline.read_history_file(history_file)
        except FileNotFoundError:
            pass
        readline.set_history_length(2000)
        atexit.register(readline.write_history_file, history_file)
    except ImportError:
        pass  # readline unavailable (e.g. Windows without pyreadline)


def run_shell(db: "Connection", db_path: str) -> None:
    """
    Start the interactive shell on *db*.

    Parameters
    ----------
    db:
        An open :class:`~hypermeshdb.Connection`.
    db_path:
        Display path shown in the banner.
    """
    from . import __version__, HyperMeshError
    from ._table import render_table, render_plan

    _setup_readline()

    print(_BANNER.format(version=__version__))
    print(f"  Database : {os.path.abspath(db_path)}")
    print(f"  Records  : {db.total_records:,}")
    print(f"  Buckets  : {db.bucket_count}  ({db.bucket_seconds}s each)")
    print(f"  Nodes    : {db.node_count:,}")
    print(f"  WAL      : {db.wal_pending} pending entries")
    print()

    while True:
        try:
            line = input("hmdb> ").strip()
        except EOFError:
            print("\nBye.")
            break
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue

        upper = line.upper()

        # ── Exit ─────────────────────────────────────────────────────────
        if upper in ("QUIT", "EXIT", "\\Q", ":Q", ".QUIT", ".EXIT"):
            print("Bye.")
            break

        # ── Help ─────────────────────────────────────────────────────────
        if upper in ("HELP", "?", "\\H", ":H", ".HELP"):
            print(_HELP_TEXT)
            continue

        # ── Dot commands ──────────────────────────────────────────────────
        if upper == ".TABLES":
            line = "CALL show_hyperedge_tables() RETURN *"

        elif upper == ".INFO":
            print(f"  database   : {os.path.abspath(db_path)}")
            print(f"  records    : {db.total_records:,}")
            print(f"  buckets    : {db.bucket_count}  ({db.bucket_seconds}s each)")
            print(f"  nodes      : {db.node_count:,}")
            print(f"  wal pend.  : {db.wal_pending}")
            print()
            continue

        elif upper == ".COMPACT":
            pending = db.wal_pending
            if pending == 0:
                print("  WAL is already empty — nothing to compact.")
                print()
                continue
            print(f"  Compacting {pending} WAL entries …", end=" ", flush=True)
            t0 = time.perf_counter()
            db.compact()
            ms = (time.perf_counter() - t0) * 1000
            print(f"done  ({ms:.1f}ms)  WAL pending: {db.wal_pending}")
            print()
            continue

        # ── Execute query ─────────────────────────────────────────────────
        t0 = time.perf_counter()
        try:
            result = db.execute(line)
        except HyperMeshError as exc:
            print(f"  Error: {exc}", file=sys.stderr)
            print()
            continue

        elapsed_ms = (time.perf_counter() - t0) * 1000

        if result.num_tuples == 0:
            print("  (0 rows)")
        else:
            raw = [r.values() for r in result]
            print(render_table(result.columns, raw))

        row_word  = "row" if result.num_tuples == 1 else "rows"
        plan_str  = render_plan(result.query_plan)
        print(f"  {result.num_tuples} {row_word}  ({elapsed_ms:.2f}ms){plan_str}")
        print()
