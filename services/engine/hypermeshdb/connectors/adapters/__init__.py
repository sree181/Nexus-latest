"""
Connector source adapters.

These subclass ``hypermesh_ingest.adapters.base.BaseAdapter`` so the live
data-source connectors (S3 / MDE / webhook) feed the *same* ingestion
pipeline as the standalone engine: ``adapter.stream() -> build() ->
emit_to_connection()``.

They live in the app layer (``hypermeshdb``) rather than inside
``hypermesh_ingest`` so the engine stays standalone — the dependency points
app -> engine, never the reverse.
"""

from .mde_adapter import MDESourceAdapter
from .s3_adapter import S3SourceAdapter
from .webhook_adapter import WebhookQueueAdapter

__all__ = ["S3SourceAdapter", "MDESourceAdapter", "WebhookQueueAdapter"]
