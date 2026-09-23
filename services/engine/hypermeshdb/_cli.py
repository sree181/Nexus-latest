"""
_cli.py — ``hmdb`` command-line interface for HyperMesh DB.

Entry point registered in pyproject.toml::

    [project.scripts]
    hmdb = "hypermeshdb._cli:main"

Subcommands
-----------
hmdb info    <db_dir>                      Database statistics
hmdb shell   <db_dir>                      Interactive Cypher shell
hmdb query   <db_dir> <cypher>             Execute one query, print table / CSV
hmdb import  <db_dir> --edges <csv>        Build TPI+FMI index from CSV
hmdb export  <db_dir> --query <cypher>     Write result to CSV file
hmdb compact <db_dir> [--ttl SECONDS] [--table TABLE]  Compact WAL into TPI+FMI; optional TTL eviction
hmdb autocompact <db_dir> --threshold N [--table TABLE] Configure auto-compact WAL threshold
hmdb insert  <db_dir> --ts N --members IDs Insert a single hyperedge record
hmdb migrate <db_dir>                      Upgrade pre-Phase-6 flat layout to partitioned
hmdb analytics <db_dir> <table> <measure> [--param K=V ...] Compute a hypergraph analytics measure
hmdb serve   <db_dir> [--host H] [--port P] [--no-auth] [--tls-cert C --tls-key K]  REST API server
hmdb add-key <db_dir> [--role ROLE] [--description TEXT] [--expires-days N]  Create an API key
hmdb revoke-expired-keys <db_dir>                                           Revoke expired API keys
hmdb list-keys <db_dir>                    List all API keys
hmdb revoke-key <db_dir> <key_id>          Revoke an API key
hmdb backup  <db_dir> <output.tar.gz>      Create compressed database backup
hmdb restore <archive.tar.gz> <db_dir>     Restore from a backup archive
"""

from __future__ import annotations

import argparse
import csv as csv_mod
import os
import sys
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _err(msg: str) -> None:
    print(f"hmdb: error: {msg}", file=sys.stderr)


def _open_db(
    db_dir:         str,
    edges_csv:      str | None = None,
    nodes_csv:      str | None = None,
    bucket_seconds: int        = 10,
):
    """
    Open or create a Connection.  Exits with code 1 on failure.
    """
    from . import connect, HyperMeshError
    try:
        return connect(
            db_dir,
            hyperedges_csv = edges_csv,
            nodes_csv      = nodes_csv,
            bucket_seconds = bucket_seconds,
        )
    except HyperMeshError as exc:
        _err(str(exc))
        sys.exit(1)


def _render(result: Any, fmt: str = "table") -> None:
    """Print a QueryResult as a Unicode table or raw CSV to stdout."""
    from ._table import render_table, render_plan

    if fmt == "csv":
        writer = csv_mod.writer(sys.stdout)
        writer.writerow(result.columns)
        for row in result:
            writer.writerow(row.values())
        return

    if result.num_tuples == 0:
        print("(0 rows)")
    else:
        raw = [r.values() for r in result]
        print(render_table(result.columns, raw))

    row_word = "row" if result.num_tuples == 1 else "rows"
    plan_str = render_plan(result.query_plan)
    print(f"{result.num_tuples} {row_word}{plan_str}")


# ── Subcommand implementations ────────────────────────────────────────────────

def cmd_info(args: argparse.Namespace) -> int:
    """Print database statistics and hyperedge table list."""
    from ._table import render_table

    with _open_db(args.db_dir) as db:
        abs_path = os.path.abspath(args.db_dir)
        print(f"  Database directory : {abs_path}")
        print(f"  Total records      : {db.total_records:,}")
        print(f"  Bucket count       : {db.bucket_count}")
        print(f"  Bucket seconds     : {db.bucket_seconds}")
        print(f"  Unique nodes       : {db.node_count:,}")
        print(f"  WAL pending        : {db.wal_pending}")

        result = db.execute("CALL show_hyperedge_tables() RETURN *")
        print("\n  Hyperedge tables:")
        raw = [r.values() for r in result]
        for line in render_table(result.columns, raw).splitlines():
            print(f"  {line}")
        print()
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    """Launch the interactive Cypher shell."""
    with _open_db(args.db_dir) as db:
        from ._shell import run_shell
        run_shell(db, args.db_dir)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Execute a single query and print results."""
    from . import HyperMeshError

    with _open_db(args.db_dir) as db:
        try:
            result = db.execute(args.cypher)
        except HyperMeshError as exc:
            _err(str(exc))
            return 1
        _render(result, fmt=args.format)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Build TPI+FMI index from CSV files."""
    abs_dir = os.path.abspath(args.db_dir)
    print(f"Building index in {abs_dir} …")

    with _open_db(
        args.db_dir,
        edges_csv      = args.edges,
        nodes_csv      = args.nodes,
        bucket_seconds = args.bucket_seconds,
    ) as db:
        print("Index built successfully.")
        print(f"  Records        : {db.total_records:,}")
        print(f"  Buckets        : {db.bucket_count}  ({db.bucket_seconds}s each)")
        print(f"  Unique nodes   : {db.node_count:,}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Execute a query and write the result to a CSV file."""
    from . import HyperMeshError

    with _open_db(args.db_dir) as db:
        try:
            result = db.execute(args.query)
        except HyperMeshError as exc:
            _err(str(exc))
            return 1

        out_path = os.path.abspath(args.output)
        with open(out_path, "w", newline="") as fh:
            writer = csv_mod.writer(fh)
            writer.writerow(result.columns)
            for row in result:
                writer.writerow(row.values())

        print(f"Exported {result.num_tuples:,} rows → {out_path}")
    return 0


def cmd_compact(args: argparse.Namespace) -> int:
    """Compact WAL entries into the TPI+FMI index."""
    import time

    ttl: int | None = getattr(args, "ttl", None)
    tbl: str | None = getattr(args, "table", None)

    with _open_db(args.db_dir) as db:
        pending = db.wal_pending
        if pending == 0 and ttl is None:
            print("WAL is already empty — nothing to compact.")
            return 0
        label = f"{pending} WAL entr{'y' if pending == 1 else 'ies'}"
        if ttl is not None:
            label += f"  (TTL={ttl}s — records older than {ttl}s will be evicted)"
        if tbl:
            label += f"  [table: {tbl}]"
        print(f"Compacting {label} …", end=" ", flush=True)
        t0 = time.perf_counter()
        db.compact(ttl_seconds=ttl, table=tbl)
        ms = (time.perf_counter() - t0) * 1000
        print(f"done  ({ms:.1f}ms)")
        print(f"WAL pending after compact: {db.wal_pending}")
    return 0


def cmd_autocompact(args: argparse.Namespace) -> int:
    """Configure automatic WAL compaction threshold for one or all tables."""
    from . import HyperMeshError

    threshold: int = args.threshold
    tbl: str | None = getattr(args, "table", None)

    with _open_db(args.db_dir) as db:
        try:
            db.set_autocompact(threshold, table=tbl)
        except HyperMeshError as exc:
            _err(str(exc))
            return 1
        if threshold == 0:
            scope = f"table '{tbl}'" if tbl else "all tables"
            print(f"Auto-compact disabled for {scope}.")
        else:
            scope = f"table '{tbl}'" if tbl else "all tables"
            print(f"Auto-compact threshold set to {threshold} WAL entries for {scope}.")
    return 0


def cmd_insert(args: argparse.Namespace) -> int:
    """Insert a single hyperedge record into the WAL."""
    from . import HyperMeshError

    try:
        members = [int(m.strip()) for m in args.members.split(",")]
    except ValueError:
        _err("--members must be a comma-separated list of integers: e.g. 1,2,3")
        return 1

    with _open_db(args.db_dir) as db:
        try:
            db.insert(
                event_ts    = args.ts,
                members     = members,
                weight      = args.weight,
                mean_dist_m = args.mean_dist,
                formation   = args.formation,
            )
        except HyperMeshError as exc:
            _err(str(exc))
            return 1

        print(
            f"Inserted: ts={args.ts}  members={members}  "
            f"weight={args.weight:.4g}  formation={args.formation!r}"
        )
        print(f"WAL pending: {db.wal_pending}")
    return 0


def cmd_copy(args: argparse.Namespace) -> int:
    """Load records from a CSV file into a table using COPY FROM semantics."""
    import time
    from . import HyperMeshError

    with _open_db(args.db_dir) as db:
        try:
            t0 = time.perf_counter()
            result = db.copy_from_csv(
                table         = args.table,
                path          = args.file,
                header        = args.header,
                delimiter     = args.delim,
                quotechar     = args.quote,
                skip          = args.skip,
                ignore_errors = args.ignore_errors,
            )
        except HyperMeshError as exc:
            _err(str(exc))
            return 1

        row = result.fetchone()
        if row is None:
            _err("COPY FROM returned no result")
            return 1

        loaded  = row["rows_loaded"]
        skipped = row["rows_skipped"]
        elapsed = row["elapsed_ms"]
        print(
            f"COPY {row['table']}: "
            f"{loaded:,} rows loaded, "
            f"{skipped} rows skipped  ({elapsed}ms)"
        )
        if skipped:
            print(f"  {skipped} rows had errors and were skipped.")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """
    Migrate a pre-Phase-6 flat-layout database to the partitioned layout.

    The flat layout stores TPI/WAL/FMI files directly in DB_DIR.  The
    partitioned layout puts them in DB_DIR/<TABLE>/ subdirectories so that
    every logical hyperedge table has its own isolated physical store.

    All existing flat-layout data is moved to DB_DIR/<PRIMARY_TABLE>/.
    schema.db and nodes*.db remain at the DB root and are untouched.
    PSI files (psi_*.bin) are moved to the primary table subdirectory.
    """
    import os
    import shutil
    import glob

    db_dir = os.path.abspath(args.db_dir)

    if not os.path.isdir(db_dir):
        _err(f"Not a directory: {db_dir}")
        return 1

    sentinel = os.path.join(db_dir, "bucket_directory.bin")
    if not os.path.exists(sentinel):
        # Check if already partitioned
        already = any(
            os.path.exists(os.path.join(db_dir, sub, "bucket_directory.bin"))
            for sub in os.listdir(db_dir)
            if os.path.isdir(os.path.join(db_dir, sub))
        )
        if already:
            print(f"✓ {db_dir} is already in partitioned layout. Nothing to do.")
        else:
            print(f"✓ {db_dir} appears to be an empty database. Nothing to migrate.")
        return 0

    # Determine the primary table name
    primary = args.primary_table
    if primary is None:
        # Try to read from schema.db
        schema_db = os.path.join(db_dir, "schema.db")
        if os.path.exists(schema_db):
            try:
                from hypermesh_core.python.schema_store import SchemaStore as _SS
                ss = _SS(schema_db)
                tables = ss.list_hyperedge_tables()
                ss.close()
                if tables:
                    primary = tables[0]["name"]
            except Exception:
                pass
        if primary is None:
            primary = "CoProximity"

    primary_dir = os.path.join(db_dir, primary.upper())
    os.makedirs(primary_dir, exist_ok=True)

    print(f"Migrating '{db_dir}' → partitioned layout")
    print(f"  Primary table partition: {primary_dir}")

    # Move the five core index files
    moved = 0
    for fname in ("bucket_directory.bin", "hyperedges.bin",
                  "wal.bin", "fmi_nodes.bin", "fmi_adjacency.bin"):
        src = os.path.join(db_dir, fname)
        dst = os.path.join(primary_dir, fname)
        if os.path.exists(src):
            if os.path.exists(dst):
                _err(
                    f"Destination already exists: {dst}\n"
                    f"Remove it manually and re-run migrate."
                )
                return 1
            shutil.move(src, dst)
            print(f"  Moved: {fname}")
            moved += 1

    # Move PSI files
    for psi_src in glob.glob(os.path.join(db_dir, "psi_*.bin")):
        psi_dst = os.path.join(primary_dir, os.path.basename(psi_src))
        shutil.move(psi_src, psi_dst)
        print(f"  Moved: {os.path.basename(psi_src)}")
        moved += 1

    if moved == 0:
        print("  No files to move.")
    else:
        print(f"  {moved} file(s) moved.")

    # Verify the migrated partition opens correctly
    try:
        from hypermesh_core.python.hm_store import HmStore as _HmStore
        s = _HmStore(primary_dir)
        rec_count = s.total_records
        s.close()
        print(f"  Verified: partition opens OK — {rec_count} records in TPI")
    except Exception as exc:
        _err(f"Post-migration verify failed: {exc}")
        return 1

    print(f"\n✓ Migration complete. Re-run your application with the same DB_DIR.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the HyperMesh DB REST API server."""
    import os

    abs_dir = os.path.abspath(args.db_dir)

    # Configure logging before anything else
    from ._logging_config import configure_logging
    configure_logging(
        level      = getattr(args, "log_level", "INFO"),
        log_file   = getattr(args, "log_file",  None),
        audit_file = getattr(args, "audit_log", None),
    )

    # ── OpenAPI dump mode (no server needed) ──────────────────────────────
    if args.dump_openapi:
        import json
        try:
            from ._api import create_app
        except ImportError as exc:
            _err(f"fastapi is required for 'serve': {exc}")
            return 1
        spec = create_app(abs_dir, auth_disabled=True).openapi()
        out_path = os.path.abspath(args.dump_openapi)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(spec, fh, indent=2)
        print(f"OpenAPI spec written to {out_path}")
        return 0

    # ── Server mode ───────────────────────────────────────────────────────
    try:
        import uvicorn
        from ._api import create_app
    except ImportError as exc:
        _err(
            f"fastapi/uvicorn is required to start the server: {exc}\n"
            "Install with:  pip install 'hypermeshdb[server]'"
        )
        return 1

    # Secure by default: auth is ON unless explicitly opted out via --no-auth
    # or HMDB_AUTH_DISABLED=1.  --auth is retained as a no-op for back-compat.
    auth_disabled = (
        getattr(args, "no_auth", False)
        or os.environ.get("HMDB_AUTH_DISABLED", "").strip() == "1"
    )
    auth_enabled = not auth_disabled
    tls_cert     = getattr(args, "tls_cert", None)
    tls_key      = getattr(args, "tls_key",  None)

    if tls_cert and not tls_key:
        _err("--tls-cert requires --tls-key")
        return 1
    if tls_key and not tls_cert:
        _err("--tls-key requires --tls-cert")
        return 1

    proto = "https" if tls_cert else "http"
    print("HyperMesh DB REST API")
    print(f"  Database     : {abs_dir}")
    print(f"  Listening    : {proto}://{args.host}:{args.port}")
    print(f"  Docs         : {proto}://{args.host}:{args.port}/docs")
    print(f"  Metrics      : {proto}://{args.host}:{args.port}/metrics")
    print(f"  Auth         : {'ENABLED' if auth_enabled else 'DISABLED (dev mode)'}")
    if tls_cert:
        print(f"  TLS cert     : {tls_cert}")
    print()

    # Create app with correct auth setting. --ui-dir (or HMDB_UI_DIR) makes this
    # one process serve the built front-end as well as the API.
    _ui_dir = getattr(args, "ui_dir", None)
    _app = create_app(abs_dir, auth_disabled=auth_disabled, ui_dir=_ui_dir)
    if _ui_dir:
        print(f"  UI           : {proto}://{args.host}:{args.port}/  (serving {_ui_dir})")

    uvicorn_kwargs: dict = dict(
        host                      = args.host,
        port                      = args.port,
        reload                    = args.reload,
        log_level                 = "warning",   # our JSON logger handles output
        timeout_graceful_shutdown = 60,          # wait up to 60 s for compaction to finish
    )
    if tls_cert:
        uvicorn_kwargs["ssl_certfile"] = tls_cert
        uvicorn_kwargs["ssl_keyfile"]  = tls_key

    uvicorn.run(_app, **uvicorn_kwargs)
    return 0


def cmd_add_key(args: argparse.Namespace) -> int:
    """Create a new API key and print the plaintext key once."""
    from ._auth import AuthStore
    store = AuthStore(args.db_dir)
    if args.expires_days is not None and args.expires_seconds is not None:
        store.close()
        _err("Use only one of --expires-days or --expires-seconds")
        return 1
    plaintext, rec = store.create_key(
        description=args.description,
        role=args.role,
        expires_in_days=args.expires_days,
        expires_in_seconds=args.expires_seconds,
    )
    store.close()
    print("API key created")
    print(f"  Key ID      : {rec.key_id}")
    print(f"  Role        : {rec.role}")
    print(f"  Description : {rec.description}")
    if rec.expires_at is not None:
        import datetime as _dt
        exp = _dt.datetime.utcfromtimestamp(rec.expires_at).strftime("%Y-%m-%d %H:%M UTC")
        print(f"  Expires     : {exp} (epoch {rec.expires_at})")
    print()
    print("  Plaintext key (shown ONCE — save it now):")
    print(f"  {plaintext}")
    return 0


def cmd_list_keys(args: argparse.Namespace) -> int:
    """List all API keys (no plaintext, only metadata)."""
    import time as _time
    from ._auth import AuthStore
    from ._table import render_table

    store = AuthStore(args.db_dir)
    keys  = store.list_keys()
    store.close()

    if not keys:
        print("No API keys found. Create one with: hmdb add-key <db_dir>")
        return 0

    rows = []
    for k in keys:
        last = "never"
        if k.last_used_at:
            diff = int(_time.time()) - k.last_used_at
            last = f"{diff}s ago" if diff < 3600 else f"{diff//3600}h ago"
        exp = "never"
        if k.expires_at is not None:
            exp = "expired" if k.is_expired() else f"in {(k.expires_at - int(_time.time())) // 86400}d"
        rows.append([k.key_id, k.role, k.description[:40], last, exp])

    print(render_table(["key_id", "role", "description", "last_used", "expires"], rows))
    print(f"{len(keys)} key(s)")
    return 0


def cmd_revoke_expired_keys(args: argparse.Namespace) -> int:
    """Revoke all API keys past their expires_at timestamp."""
    from ._auth import AuthStore

    store = AuthStore(args.db_dir)
    revoked = store.revoke_expired()
    store.close()
    if revoked:
        print(f"Revoked {len(revoked)} expired key(s): {', '.join(revoked)}")
    else:
        print("No expired keys to revoke.")
    return 0


def cmd_revoke_key(args: argparse.Namespace) -> int:
    """Revoke an API key by key_id."""
    from ._auth import AuthStore

    store   = AuthStore(args.db_dir)
    deleted = store.revoke(args.key_id)
    store.close()

    if deleted:
        print(f"Key '{args.key_id}' revoked.")
    else:
        _err(f"Key '{args.key_id}' not found.")
        return 1
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Create a gzip-compressed tar backup of the database directory."""
    import tarfile as _tar
    import time as _time

    src  = os.path.abspath(args.db_dir)
    dest = os.path.abspath(args.output)

    if not os.path.isdir(src):
        _err(f"Database directory not found: {src}")
        return 1

    if args.compact:
        print("Running compaction before backup …")
        with _open_db(src) as db:
            from . import HyperMeshError
            try:
                db.compact()
                print(f"  Compacted — WAL pending: {db.wal_pending}")
            except HyperMeshError as exc:
                _err(f"Compaction failed: {exc}")
                return 1

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    print(f"Backing up {src} → {dest} …")
    t0 = _time.monotonic()
    with _tar.open(dest, "w:gz") as tf:
        tf.add(src, arcname=os.path.basename(src))
    elapsed = _time.monotonic() - t0
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"  Backup complete: {size_mb:.1f} MB in {elapsed:.1f}s")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a database from a backup archive."""
    import shutil
    import tarfile as _tar
    import tempfile as _tempfile

    src  = os.path.abspath(args.archive)
    dest = os.path.abspath(args.db_dir)

    if not os.path.isfile(src):
        _err(f"Archive not found: {src}")
        return 1

    if os.path.exists(dest) and not args.force:
        _err(
            f"Destination already exists: {dest}\n"
            "Use --force to overwrite."
        )
        return 1

    print(f"Restoring {src} → {dest} …")

    with _tempfile.TemporaryDirectory() as staging:
        with _tar.open(src, "r:gz") as tf:
            tf.extractall(path=staging)

        # The archive root dir may be named differently from dest.
        # Find the single top-level entry and rename it to dest.
        entries = os.listdir(staging)
        if len(entries) != 1 or not os.path.isdir(os.path.join(staging, entries[0])):
            # Flat archive (no wrapper dir) — move the whole staging dir
            extracted = staging
        else:
            extracted = os.path.join(staging, entries[0])

        if os.path.exists(dest) and args.force:
            shutil.rmtree(dest)
        shutil.copytree(extracted, dest)

    print("  Restore complete. Verifying …")

    schema_path = os.path.join(dest, "schema.db")
    if not os.path.exists(schema_path):
        _err("schema.db not found in restored directory — archive may be corrupt")
        return 1

    print("  schema.db found — restore looks healthy.")
    print(f"  Start server with: hmdb serve {dest}")
    return 0


def cmd_analytics(args: argparse.Namespace) -> int:
    """
    Compute a hypergraph analytics measure for a table.

    Materialises the table into a sparse incidence matrix (via a full scan)
    and runs the requested measure.  Optional parameters are parsed from
    ``--param KEY=VALUE`` flags (integers/floats auto-coerced).
    """
    import json as _json

    db = _open_db(args.db_dir)

    # Parse --param KEY=VALUE flags
    params: dict[str, Any] = {}
    for kv in getattr(args, "params", []) or []:
        if "=" not in kv:
            _err(f"--param must be KEY=VALUE, got: {kv!r}")
            db.close()
            return 1
        k, v = kv.split("=", 1)
        k = k.strip()
        # Auto-coerce: try int, then float, then keep as string
        for coerce in (int, float, str):
            try:
                v = coerce(v)   # type: ignore[assignment]
                break
            except (ValueError, TypeError):
                pass
        params[k] = v

    try:
        an = db.analytics(args.table)
    except Exception as exc:
        _err(f"Failed to build hypergraph for '{args.table}': {exc}")
        db.close()
        return 1

    fn = getattr(an, args.measure, None)
    if fn is None:
        _err(
            f"Unknown measure '{args.measure}'. "
            f"Run 'hmdb analytics --help' for the full list."
        )
        db.close()
        return 1

    try:
        result = fn(**params)
    except Exception as exc:
        _err(f"{args.measure}() failed: {exc}")
        db.close()
        return 1
    finally:
        db.close()

    if getattr(args, "json", False):
        # Raw JSON output (pipe-friendly)
        def _default(o: object) -> object:
            if isinstance(o, (list, tuple)) and len(o) == 3:
                return list(o)
            raise TypeError
        print(_json.dumps(result, default=str))
        return 0

    # Human-readable output
    if isinstance(result, dict):
        # key → value table
        first_key = next(iter(result), None)
        if isinstance(first_key, tuple):
            # intersection_profile / s_adjacency: (u, v) → count
            rows_data = [list(k) + [v] for k, v in sorted(result.items())]
            header = ["node_u", "node_v", "value"]
        else:
            rows_data = [[k, v] for k, v in sorted(result.items())]
            header = ["node_id", "value"]
        from hypermeshdb._result import QueryResult
        qr = QueryResult(columns=header, rows=rows_data)
        _render(qr)
    elif isinstance(result, (list, tuple)):
        from hypermeshdb._result import QueryResult
        rows_data = [[i, v] for i, v in enumerate(result)]
        qr = QueryResult(columns=["index", "value"], rows=rows_data)
        _render(qr)
    else:
        print(f"{args.measure}: {result}")
    return 0


# ── Argument parser ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog        = "hmdb",
        description = (
            "HyperMesh DB — Temporal Hypergraph Database  v{v}\n\n"
            "Each subcommand operates on a database directory (DB_DIR).\n"
            "Run 'hmdb import' first to build the index from CSV data."
        ).format(v=__version__),
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = (
            "Examples:\n"
            "  hmdb import  ./my_db --edges hyperedges.csv --nodes nodes.csv\n"
            "  hmdb copy    ./my_db CoProximity --from edges.csv --header\n"
            "  hmdb copy    ./my_db Drone --from nodes.csv --header --ignore-errors\n"
            "  hmdb info    ./my_db\n"
            "  hmdb shell   ./my_db\n"
            "  hmdb query   ./my_db "
            "\"MATCH HYPEREDGE (he:CoProximity) WHERE he.event_ts >= 0 AND he.event_ts <= 100 RETURN *\"\n"
            "  hmdb export  ./my_db --query \"MATCH HYPEREDGE (he:CoProximity) RETURN *\" --output out.csv\n"
            "  hmdb insert  ./my_db --ts 9999 --members 1,2,3 --weight 0.9\n"
            "  hmdb compact ./my_db\n"
            "  hmdb serve   ./my_db --port 8000\n"
            "  hmdb serve   ./my_db --dump-openapi docs/openapi.json\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"hmdb {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── info ──────────────────────────────────────────────────────────────
    p = sub.add_parser("info", help="Show database statistics and table list")
    p.add_argument("db_dir", metavar="DB_DIR", help="Database directory")
    p.set_defaults(func=cmd_info)

    # ── shell ─────────────────────────────────────────────────────────────
    p = sub.add_parser("shell",
                       help="Start an interactive Cypher query shell")
    p.add_argument("db_dir", metavar="DB_DIR")
    p.set_defaults(func=cmd_shell)

    # ── query ─────────────────────────────────────────────────────────────
    p = sub.add_parser("query", help="Execute a single Cypher query and print results")
    p.add_argument("db_dir",  metavar="DB_DIR")
    p.add_argument("cypher",  metavar="CYPHER",  help="Cypher query string")
    p.add_argument(
        "--format", choices=["table", "csv"], default="table",
        help="Output format: 'table' (default) or 'csv'",
    )
    p.set_defaults(func=cmd_query)

    # ── import ────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "import",
        help="Build TPI+FMI index from CSV data",
        description=(
            "Builds the Temporal Property Index (TPI) and Forward Member Index (FMI)\n"
            "from a hyperedges CSV file.  The database directory is created if absent.\n\n"
            "Required CSV columns for --edges:\n"
            "  event_ts, members, member_count, weight, mean_dist_m, formation\n\n"
            "Required CSV columns for --nodes:\n"
            "  drone_id, callsign, role, formation, mission, avg_battery,\n"
            "  avg_signal, collision_events"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir",  metavar="DB_DIR")
    p.add_argument("--edges", required=True, metavar="FILE",
                   help="Path to hyperedges CSV file")
    p.add_argument("--nodes", metavar="FILE",
                   help="Path to node-properties CSV file (optional)")
    p.add_argument("--bucket-seconds", type=int, default=10, metavar="N",
                   help="TPI time-bucket width in seconds (default: 10)")
    p.set_defaults(func=cmd_import)

    # ── export ────────────────────────────────────────────────────────────
    p = sub.add_parser("export", help="Export query results to a CSV file")
    p.add_argument("db_dir",   metavar="DB_DIR")
    p.add_argument("--query",  required=True, metavar="CYPHER",
                   help="Query whose result set to export")
    p.add_argument("--output", "-o", required=True, metavar="FILE",
                   help="Destination CSV file path")
    p.set_defaults(func=cmd_export)

    # ── compact ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "compact",
        help="Compact WAL into TPI+FMI (removes all pending inserts/tombstones)",
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.add_argument("--ttl", type=int, default=None, metavar="SECONDS",
                   help=(
                       "Also evict records whose event_ts < now - SECONDS.  "
                       "E.g. --ttl 86400 discards records older than 1 day."
                   ))
    p.add_argument("--table", default=None, metavar="TABLE",
                   help="Compact only this table (default: all tables)")
    p.set_defaults(func=cmd_compact)

    # ── autocompact ───────────────────────────────────────────────────────
    p = sub.add_parser(
        "autocompact",
        help="Configure automatic WAL compaction threshold",
        description=(
            "Set the WAL entry count at which the engine automatically compacts "
            "a table after each write.  Pass --threshold 0 to disable.\n\n"
            "The setting is persisted in schema.db and restored on every open."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.add_argument("--threshold", type=int, required=True,
                   metavar="N",
                   help=(
                       "WAL entry count that triggers auto-compact.  "
                       "0 = disable.  Typical edge value: 200."
                   ))
    p.add_argument("--table", default=None, metavar="TABLE",
                   help="Apply only to this table (default: all tables)")
    p.set_defaults(func=cmd_autocompact)

    # ── serve ─────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "serve",
        help        = "Start the REST API server (requires fastapi + uvicorn)",
        description = (
            "Starts a uvicorn ASGI server exposing the HyperMesh DB REST API.\n\n"
            "API documentation is available at http://<host>:<port>/docs once running.\n\n"
            "Use --dump-openapi to export the OpenAPI JSON spec without starting the server."
        ),
        formatter_class = argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.add_argument("--host",  default="127.0.0.1",
                   help="Bind host (default: 127.0.0.1)")
    p.add_argument("--port",  type=int, default=8000,
                   help="Bind port (default: 8000)")
    p.add_argument("--reload", action="store_true",
                   help="Enable auto-reload on source changes (development only)")
    p.add_argument("--ui-dir", metavar="DIR", default=None, dest="ui_dir",
                   help="Serve a built single-page app (with index.html) from "
                        "this directory alongside the API, so the whole product "
                        "runs as one process. Also settable via HMDB_UI_DIR.")
    p.add_argument("--dump-openapi", metavar="FILE", default=None,
                   help="Write the OpenAPI JSON spec to FILE and exit (no server started)")
    p.add_argument("--auth", action="store_true", default=False,
                   help="(deprecated) Authentication is now enabled by default; "
                        "this flag is a no-op kept for back-compat")
    p.add_argument("--no-auth", action="store_true", default=False, dest="no_auth",
                   help="Disable API key authentication — INSECURE, local dev only")
    p.add_argument("--tls-cert", metavar="CERT.PEM", default=None,
                   dest="tls_cert",
                   help="Path to TLS certificate file (enables HTTPS)")
    p.add_argument("--tls-key",  metavar="KEY.PEM",  default=None,
                   dest="tls_key",
                   help="Path to TLS private key file (required with --tls-cert)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   dest="log_level",
                   help="Console log level (default: INFO)")
    p.add_argument("--log-file",  default=None, metavar="PATH",
                   dest="log_file",
                   help="Write all logs to this file in addition to stderr")
    p.add_argument("--audit-log", default=None, metavar="PATH",
                   dest="audit_log",
                   help="Write audit events (DDL, DML, auth) to this file")
    p.set_defaults(func=cmd_serve)

    # ── add-key ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "add-key",
        help        = "Create a new API key",
        description = (
            "Generates a new API key and stores its SHA-256 hash in schema.db.\n"
            "The plaintext key is printed ONCE — save it immediately.\n\n"
            "Examples:\n"
            "  hmdb add-key /data/mydb --role admin --description 'CI pipeline'\n"
            "  hmdb add-key /data/mydb --role readonly --description 'Dashboard'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.add_argument("--role", default="readonly",
                   choices=["readonly", "readwrite", "admin"],
                   help="Permission level (default: readonly)")
    p.add_argument("--description", default="", metavar="TEXT",
                   help="Human-readable description for this key")
    p.add_argument("--expires-days", type=int, default=None, metavar="N",
                   help="Key expires N days from now (eval handoff default: 30–60)")
    p.add_argument("--expires-seconds", type=int, default=None, metavar="N",
                   help="Key expires N seconds from now (testing / short-lived keys)")
    p.set_defaults(func=cmd_add_key)

    # ── list-keys ─────────────────────────────────────────────────────────
    p = sub.add_parser("list-keys", help="List all API keys")
    p.add_argument("db_dir", metavar="DB_DIR")
    p.set_defaults(func=cmd_list_keys)

    # ── revoke-expired-keys ───────────────────────────────────────────────
    p = sub.add_parser(
        "revoke-expired-keys",
        help="Revoke all API keys past their expiry time",
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.set_defaults(func=cmd_revoke_expired_keys)

    # ── revoke-key ────────────────────────────────────────────────────────
    p = sub.add_parser("revoke-key", help="Revoke an API key by key_id")
    p.add_argument("db_dir",  metavar="DB_DIR")
    p.add_argument("key_id",  metavar="KEY_ID",
                   help="The key_id to revoke (from hmdb list-keys)")
    p.set_defaults(func=cmd_revoke_key)

    # ── backup ────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "backup",
        help        = "Create a compressed backup of the database directory",
        description = (
            "Archives the entire database directory as a gzip-compressed tar file.\n"
            "Use --compact to flush the WAL before archiving (recommended).\n\n"
            "Examples:\n"
            "  hmdb backup /data/mydb /backups/mydb_$(date +%Y%m%d).tar.gz\n"
            "  hmdb backup /data/mydb /backups/mydb.tar.gz --no-compact\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir",  metavar="DB_DIR",
                   help="Database directory to back up")
    p.add_argument("output",  metavar="OUTPUT.tar.gz",
                   help="Output archive path")
    p.add_argument("--compact", dest="compact", action="store_true", default=True,
                   help="Compact WAL before backup (default: on)")
    p.add_argument("--no-compact", dest="compact", action="store_false",
                   help="Skip compaction before backup")
    p.set_defaults(func=cmd_backup)

    # ── restore ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "restore",
        help        = "Restore a database from a backup archive",
        description = (
            "Extracts a backup archive created by 'hmdb backup' to a directory.\n"
            "Use --force to overwrite an existing directory.\n\n"
            "Example:\n"
            "  hmdb restore /backups/mydb_20260410.tar.gz /data/mydb-restored\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("archive", metavar="ARCHIVE.tar.gz",
                   help="Backup archive created by 'hmdb backup'")
    p.add_argument("db_dir",  metavar="DB_DIR",
                   help="Destination directory (must not exist unless --force)")
    p.add_argument("--force", action="store_true",
                   help="Overwrite destination if it already exists")
    p.set_defaults(func=cmd_restore)

    # ── copy ──────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "copy",
        help="Load records from a CSV file into a table (COPY FROM semantics)",
        description=(
            "Reads a CSV file and bulk-loads its rows into a HyperMesh DB table.\n"
            "Equivalent to: COPY <table> FROM '<file>' (<options>)\n\n"
            "Hyperedge CSV columns: event_ts, members, member_count, weight, mean_dist_m, formation\n"
            "Node CSV columns     : drone_id, callsign, role, formation, mission,\n"
            "                       avg_battery, avg_signal, collision_events\n\n"
            "Examples:\n"
            "  hmdb copy ./my_db CoProximity --from edges.csv --header\n"
            "  hmdb copy ./my_db Drone --from nodes.csv --header --delim '|'\n"
            "  hmdb copy ./my_db CoProximity --from edges.csv --skip 1 --ignore-errors\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir", metavar="DB_DIR")
    p.add_argument("table",  metavar="TABLE",
                   help="Target table name (e.g. CoProximity or Drone)")
    p.add_argument("--from", dest="file", required=True, metavar="FILE",
                   help="Path to the CSV file to load")
    p.add_argument("--header", action="store_true", default=None,
                   help="First row is a header (default: auto-detect)")
    p.add_argument("--no-header", dest="header", action="store_false",
                   help="File has no header row")
    p.add_argument("--delim", default=",", metavar="CHAR",
                   help="Column delimiter character (default: ',')")
    p.add_argument("--quote", default='"', metavar="CHAR",
                   help="Quote character (default: '\"')")
    p.add_argument("--skip", type=int, default=0, metavar="N",
                   help="Number of leading rows to skip before header/data (default: 0)")
    p.add_argument("--ignore-errors", action="store_true", dest="ignore_errors",
                   help="Skip malformed rows instead of raising an error")
    p.set_defaults(func=cmd_copy)

    # ── insert ────────────────────────────────────────────────────────────
    p = sub.add_parser("insert", help="Insert a single hyperedge record into the WAL")
    p.add_argument("db_dir",    metavar="DB_DIR")
    p.add_argument("--ts",      type=int,   required=True, metavar="SECONDS",
                   help="Event timestamp in seconds")
    p.add_argument("--members", required=True, metavar="N1,N2,...",
                   help="Comma-separated member node IDs")
    p.add_argument("--weight",    type=float, default=0.0,
                   help="Coalition weight 0–1 (default: 0.0)")
    p.add_argument("--mean-dist", type=float, default=0.0, dest="mean_dist",
                   metavar="METRES",
                   help="Mean pairwise distance in metres (default: 0.0)")
    p.add_argument("--formation", default="",
                   help="Formation name string (default: '')")
    p.set_defaults(func=cmd_insert)

    # ── migrate ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "migrate",
        help="Upgrade a pre-Phase-6 flat-layout database to partitioned layout",
        description=(
            "Move TPI/WAL/FMI/PSI files from the database root directory into a "
            "per-table subdirectory, enabling Phase 6 physical table isolation. "
            "schema.db and nodes*.db are left in place. "
            "This operation is safe to re-run on an already-migrated database."
        ),
    )
    p.add_argument("db_dir", metavar="DB_DIR",
                   help="Path to the HyperMesh DB directory to migrate")
    p.add_argument(
        "--primary-table",
        default=None,
        dest="primary_table",
        metavar="TABLE",
        help=(
            "Name of the primary (existing) hyperedge table whose data occupies "
            "the flat layout.  Defaults to the first table in schema.db, or "
            "\"CoProximity\" if schema.db is absent."
        ),
    )
    p.set_defaults(func=cmd_migrate)

    # ── analytics ─────────────────────────────────────────────────────────
    p = sub.add_parser(
        "analytics",
        help="Compute a hypergraph analytics measure for a table",
        description=(
            "Materialises TABLE into a sparse incidence matrix and runs MEASURE.\n\n"
            "Structural : node_degree  hyperedge_size  density  redundancy\n"
            "             intersection_profile\n"
            "Centrality : weighted_degree  eigenvector_centrality  pagerank\n"
            "             katz_centrality  hedc\n"
            "Spectral   : zhou_laplacian_eigenvalues  spectral_gap  cheeger_constant\n"
            "Clustering : zhou_clustering  pairwise_clustering  global_transitivity\n"
            "Modularity : hypermodularity\n"
            "s-Walk     : s_adjacency  s_distance  s_closeness  s_betweenness\n"
            "             s_diameter  s_efficiency\n"
            "Temporal   : hyperedge_persistence  burstiness  temporal_degree_entropy\n"
            "Summary    : summary\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db_dir",  metavar="DB_DIR")
    p.add_argument("table",   metavar="TABLE",   help="Hyperedge table name")
    p.add_argument("measure", metavar="MEASURE", help="Analytics measure to compute")
    p.add_argument(
        "--param",
        action="append",
        dest="params",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Optional keyword argument passed to the measure function "
            "(repeatable).  Integers and floats are auto-coerced.  "
            "Example: --param s=2  --param alpha=0.1  --param node_id=5"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print result as raw JSON instead of a human-readable table",
    )
    p.set_defaults(func=cmd_analytics)

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    """
    Main entry point registered as the ``hmdb`` console script.
    ``argv`` is injected only in tests; omit for normal CLI use.
    """
    parser = _build_parser()
    args   = parser.parse_args(argv)
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
