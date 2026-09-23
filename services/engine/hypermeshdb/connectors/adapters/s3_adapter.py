"""
S3 / Azure Blob source adapter.

Lists + downloads objects from a bucket/container, parses each file with the
shared loader, auto-detects entity columns, and yields IR.  Only objects with
``LastModified`` newer than ``since`` are streamed (incremental watermark);
the newest timestamp seen is exposed as ``cursor`` after streaming.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Iterator, Optional

from hypermesh_ingest.adapters.base import BaseAdapter, IRRecord
from hypermesh_ingest.spec.schema import MappingSpec

from ._records import records_to_ir

_SUPPORTED = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet", ".xlsx", ".xls")


class S3SourceAdapter(BaseAdapter):
    """Amazon S3 / Azure Blob / S3-compatible object storage."""

    def __init__(
        self,
        *,
        credentials: dict,
        settings: dict,
        spec: MappingSpec,
        target_table: str,
        is_azure: bool = False,
        since: Optional[float] = None,
        limit: int = 10_000,
    ) -> None:
        super().__init__(spec)
        self._creds = credentials
        self._settings = settings
        self._target = target_table
        self._is_azure = is_azure
        self._since = since or 0.0
        self._limit = limit
        self.cursor: float = self._since  # newest LastModified seen this run

    @property
    def source_id(self) -> str:
        return f"s3://{self._settings.get('bucket', '')}/{self._settings.get('prefix', '')}"

    # ── object-store clients (ported from S3Connector) ──────────────────────

    def _s3_client(self):
        import boto3
        kwargs: dict[str, Any] = {
            "aws_access_key_id": self._creds.get("aws_access_key_id"),
            "aws_secret_access_key": self._creds.get("aws_secret_access_key"),
            "region_name": self._creds.get("region_name", "us-east-1"),
        }
        if ep := self._settings.get("endpoint_url"):
            kwargs["endpoint_url"] = ep
        return boto3.client("s3", **kwargs)

    def _azure_client(self, container: str):
        from azure.storage.blob import ContainerClient
        if cs := self._creds.get("connection_string"):
            return ContainerClient.from_connection_string(cs, container)
        acct = self._creds.get("account_name", "")
        key = self._creds.get("account_key", "")
        url = f"https://{acct}.blob.core.windows.net/{container}"
        return ContainerClient(url, credential=key)

    def _list_objects(self) -> list[tuple[str, float]]:
        """Return ``[(key, last_modified_epoch)]`` matching the filters and newer than ``since``."""
        bucket = self._settings.get("bucket", "")
        prefix = self._settings.get("prefix", "")
        pattern = self._settings.get("file_pattern", "")
        max_files = int(self._settings.get("max_files", 5))

        found: list[tuple[str, float]] = []
        if self._is_azure:
            client = self._azure_client(bucket)
            for b in client.list_blobs(name_starts_with=prefix or None):
                lm = getattr(b, "last_modified", None)
                ts = lm.timestamp() if lm else 0.0
                found.append((b.name, ts))
        else:
            s3 = self._s3_client()
            pag = s3.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=bucket, Prefix=prefix or ""):
                for obj in page.get("Contents", []):
                    lm = obj.get("LastModified")
                    ts = lm.timestamp() if lm else 0.0
                    found.append((obj["Key"], ts))
                if len(found) >= max_files * 20:
                    break

        # Extension + glob filter, then incremental watermark, then cap.
        out = []
        for key, ts in found:
            if not key.lower().endswith(_SUPPORTED):
                continue
            if pattern and not fnmatch.fnmatch(key, pattern):
                continue
            if ts <= self._since:
                continue
            out.append((key, ts))
        out.sort(key=lambda kt: kt[1])  # oldest first → deterministic
        return out[:max_files]

    def _download(self, key: str) -> bytes:
        bucket = self._settings.get("bucket", "")
        if self._is_azure:
            return self._azure_client(bucket).get_blob_client(key).download_blob().readall()
        s3 = self._s3_client()
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    # ── stream ───────────────────────────────────────────────────────────────

    def stream(self) -> Iterator[IRRecord]:
        # Parsing + entity detection are reused from the app's loader.
        from hypermeshdb.ingest.generic import infer_schema, load_records

        override = self._settings.get("ingest_config") or {}
        for key, ts in self._list_objects():
            try:
                content = self._download(key)
                records, _total, _meta = load_records(content, key.split("/")[-1])
            except Exception:
                continue
            if not records:
                self.cursor = max(self.cursor, ts)
                continue

            schema = infer_schema(records, sample_n=200)
            entity_cols = override.get("entity_columns") or [
                c.name for c in schema.columns if c.role == "entity"
            ]
            ts_col = override.get("ts_column", schema.suggested_ts_col or "")
            etypes = override.get("entity_types") or {
                c.name: c.entity_type for c in schema.columns if c.role == "entity"
            }

            yield from records_to_ir(
                records[: self._limit],
                entity_cols=entity_cols,
                ts_col=ts_col or None,
                target_table=self._target,
                source_id=self.source_id,
                spec=self.spec,
                entity_types=etypes,
            )
            self.cursor = max(self.cursor, ts)
