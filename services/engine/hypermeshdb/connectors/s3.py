"""
connectors/s3.py — Amazon S3 / Azure Blob / S3-compatible object storage connector.

Discovers and downloads data files from a bucket/container, then runs them
through the HyperMesh generic ingestion engine.

Supported file formats (auto-detected by extension):
  CSV, TSV, JSON, JSON-Lines, Parquet, XLSX

Configuration
-------------
ConnectorConfig.credentials:
  For S3:
    aws_access_key_id     : str
    aws_secret_access_key : str
    region_name           : str   (default: us-east-1)
  For Azure Blob (type=azure_blob):
    connection_string     : str   (full Azure connection string)
    OR account_name + account_key

ConnectorConfig.settings:
  bucket            : str   bucket/container name
  prefix            : str   object key prefix filter (e.g. "logs/")
  file_pattern      : str   glob-style suffix filter (e.g. "*.csv")
  max_files         : int   max files per sync (default 10)
  endpoint_url      : str   override for MinIO / S3-compatible stores
  ingest_config     : dict  passed verbatim to IngestionConfig (optional)
"""

from __future__ import annotations

import io
import time
from typing import Any

from hypermeshdb.connectors.base import ConnectorBase, ConnectorConfig, SyncResult


class S3Connector(ConnectorBase):
    """
    Amazon S3 / Azure Blob / S3-compatible connector.

    Uses ``boto3`` for S3 and ``azure-storage-blob`` for Azure Blob.
    Falls back gracefully with an informative error when the optional
    library is not installed.
    """

    # ── Internal helpers ──────────────────────────────────────────────────

    def _is_azure(self) -> bool:
        return self._cfg.type == "azure_blob"

    def _s3_client(self):
        try:
            import boto3
        except ImportError:
            raise RuntimeError(
                "boto3 is required for S3 connectors. "
                "Install it: pip install boto3"
            )
        creds    = self._cfg.credentials
        settings = self._cfg.settings
        kwargs: dict[str, Any] = {
            "aws_access_key_id":     creds.get("aws_access_key_id"),
            "aws_secret_access_key": creds.get("aws_secret_access_key"),
            "region_name":           creds.get("region_name", "us-east-1"),
        }
        if ep := settings.get("endpoint_url"):
            kwargs["endpoint_url"] = ep
        return boto3.client("s3", **kwargs)

    def _azure_client(self, container: str):
        try:
            from azure.storage.blob import ContainerClient
        except ImportError:
            raise RuntimeError(
                "azure-storage-blob is required for Azure Blob connectors. "
                "Install it: pip install azure-storage-blob"
            )
        creds = self._cfg.credentials
        if cs := creds.get("connection_string"):
            return ContainerClient.from_connection_string(cs, container)
        acct = creds.get("account_name", "")
        key  = creds.get("account_key", "")
        url  = f"https://{acct}.blob.core.windows.net/{container}"
        from azure.storage.blob import ContainerClient as CC
        from azure.core.credentials import AzureKeyCredential
        return CC(url, credential=key)

    def _list_files(self, bucket: str, prefix: str, pattern: str, max_files: int) -> list[str]:
        """Return a list of object keys matching the filter."""
        import fnmatch

        if self._is_azure():
            client = self._azure_client(bucket)
            blobs  = client.list_blobs(name_starts_with=prefix or None)
            keys   = [b.name for b in blobs]
        else:
            s3  = self._s3_client()
            pag = s3.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in pag.paginate(Bucket=bucket, Prefix=prefix or ""):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
                if len(keys) >= max_files * 10:
                    break

        # Filter by extension
        supported = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet", ".xlsx", ".xls")
        keys = [k for k in keys if k.lower().endswith(supported)]

        # Apply glob pattern
        if pattern:
            keys = [k for k in keys if fnmatch.fnmatch(k, pattern)]

        return keys[:max_files]

    def _download_bytes(self, bucket: str, key: str) -> bytes:
        if self._is_azure():
            client = self._azure_client(bucket)
            blob   = client.get_blob_client(key)
            return blob.download_blob().readall()
        else:
            s3  = self._s3_client()
            obj = s3.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()

    # ── ConnectorBase interface ───────────────────────────────────────────

    def test_connection(self) -> dict[str, Any]:
        t0      = time.perf_counter()
        bucket  = self._cfg.settings.get("bucket", "")
        if not bucket:
            return {"ok": False, "message": "No bucket/container configured", "latency_ms": 0}
        try:
            if self._is_azure():
                client = self._azure_client(bucket)
                list(client.list_blobs(results_per_page=1))
            else:
                s3 = self._s3_client()
                s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {"ok": True, "message": f"Connected to bucket '{bucket}'", "latency_ms": ms}
        except Exception as exc:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {"ok": False, "message": str(exc), "latency_ms": ms}

    def preview_schema(self, sample_n: int = 5) -> dict[str, Any]:
        from hypermeshdb.ingest.generic import load_records, infer_schema
        settings = self._cfg.settings
        bucket   = settings.get("bucket", "")
        prefix   = settings.get("prefix", "")
        pattern  = settings.get("file_pattern", "")
        try:
            keys = self._list_files(bucket, prefix, pattern, max_files=1)
            if not keys:
                return {"columns": [], "sample_rows": [], "total_est": 0, "error": "No matching files"}
            content = self._download_bytes(bucket, keys[0])
            records, total, _meta = load_records(content, keys[0].split("/")[-1])
            schema  = infer_schema(records, sample_n=50)
            cols    = [c.name for c in schema.columns]
            return {"columns": cols, "sample_rows": records[:5], "total_est": total, "file": keys[0]}
        except Exception as exc:
            return {"columns": [], "sample_rows": [], "total_est": None, "error": str(exc)}

    def sync(self, limit: int = 10_000) -> SyncResult:
        """Stream matching objects through the hypermesh_ingest pipeline."""
        from hypermeshdb.connectors.adapters import S3SourceAdapter
        from hypermeshdb.connectors.spec_bridge import connector_spec

        if not self._cfg.settings.get("bucket", ""):
            return SyncResult(0, 0, 1, 0.0, "No bucket configured")

        spec = connector_spec(self._cfg)
        adapter = S3SourceAdapter(
            credentials=self._cfg.credentials,
            settings=self._cfg.settings,
            spec=spec,
            target_table=(self._cfg.target_table or "S3_DATA").upper(),
            is_azure=self._is_azure(),
            since=self.read_cursor(),
            limit=limit,
        )
        return self.run_engine_sync(adapter, mode="append")
