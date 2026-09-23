"""
rag/registry.py — HyperGraphRAG Model Registry

Stores named model configurations (OpenAI, Ollama, local GGUF) and tracks
the currently active backend. Persisted to {db_dir}/rag_models.json.

Registry entry shape
--------------------
  {
    "id":          "ollama-phi3",
    "name":        "Phi-3 Mini (Ollama)",
    "type":        "openai" | "ollama" | "llama_cpp",
    "model":       "phi3:mini",           # model name / path
    "base_url":    "http://localhost:...", # for openai/ollama
    "gguf_path":   "/path/to/model.gguf", # for llama_cpp
    "n_gpu_layers": -1,                   # for llama_cpp
    "api_key":     "",                    # optional
    "added_at":    "2026-04-09T...",
    "last_used":   null | "2026-04-09T...",
    "perf": {
      "avg_latency_ms": 1250.0,
      "call_count": 42
    }
  }
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


_DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "id":       "openai-gpt4o-mini",
        "name":     "GPT-4o Mini (OpenAI)",
        "type":     "openai",
        "model":    "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key":  "",
        "added_at": "2026-01-01T00:00:00Z",
        "last_used": None,
        "perf": {"avg_latency_ms": 0.0, "call_count": 0},
    },
    {
        "id":       "openai-gpt4o",
        "name":     "GPT-4o (OpenAI)",
        "type":     "openai",
        "model":    "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "api_key":  "",
        "added_at": "2026-01-01T00:00:00Z",
        "last_used": None,
        "perf": {"avg_latency_ms": 0.0, "call_count": 0},
    },
    {
        "id":       "ollama-phi3",
        "name":     "Phi-3 Mini (Ollama)",
        "type":     "ollama",
        "model":    "phi3:mini",
        "base_url": "http://localhost:11434/v1",
        "api_key":  "ollama",
        "added_at": "2026-01-01T00:00:00Z",
        "last_used": None,
        "perf": {"avg_latency_ms": 0.0, "call_count": 0},
    },
]


class ModelRegistry:
    """
    Persistent model registry for HyperGraphRAG backends.

    Parameters
    ----------
    db_dir :
        Root database directory (same as used throughout the pipeline).
    """

    def __init__(self, db_dir: str = "data") -> None:
        self._path = Path(db_dir) / "rag_models.json"
        self._data = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text("utf-8"))
            except Exception:
                pass
        # Seed with defaults
        default: dict[str, Any] = {
            "active_id": "openai-gpt4o-mini",
            "models":    _DEFAULT_MODELS[:],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(default, indent=2, ensure_ascii=False), "utf-8")
        return default

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict[str, Any]]:
        return self._data.get("models", [])

    def get_active(self) -> dict[str, Any] | None:
        active_id = self._data.get("active_id")
        for m in self._data.get("models", []):
            if m["id"] == active_id:
                return m
        return None

    def activate(self, model_id: str) -> dict[str, Any]:
        for m in self._data.get("models", []):
            if m["id"] == model_id:
                self._data["active_id"] = model_id
                m["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save()
                return m
        raise KeyError(f"Model {model_id!r} not found in registry")

    def register(
        self,
        name:         str,
        model_type:   str,
        model:        str,
        base_url:     str  = "",
        api_key:      str  = "",
        gguf_path:    str  = "",
        n_gpu_layers: int  = -1,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id":          str(uuid.uuid4())[:8],
            "name":        name,
            "type":        model_type,
            "model":       model,
            "base_url":    base_url,
            "api_key":     api_key,
            "gguf_path":   gguf_path,
            "n_gpu_layers": n_gpu_layers,
            "added_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_used":   None,
            "perf":        {"avg_latency_ms": 0.0, "call_count": 0},
        }
        self._data.setdefault("models", []).append(entry)
        self._save()
        return entry

    def remove(self, model_id: str) -> bool:
        before = len(self._data.get("models", []))
        self._data["models"] = [
            m for m in self._data.get("models", []) if m["id"] != model_id
        ]
        if self._data.get("active_id") == model_id:
            self._data["active_id"] = (
                self._data["models"][0]["id"] if self._data["models"] else None
            )
        changed = len(self._data["models"]) < before
        if changed:
            self._save()
        return changed

    def record_call(self, model_id: str, latency_ms: float) -> None:
        """Update running average latency for a model."""
        for m in self._data.get("models", []):
            if m["id"] == model_id:
                perf = m.setdefault("perf", {"avg_latency_ms": 0.0, "call_count": 0})
                n   = perf["call_count"]
                perf["avg_latency_ms"] = (perf["avg_latency_ms"] * n + latency_ms) / (n + 1)
                perf["call_count"]     = n + 1
                m["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save()
                return

    # ── LLMConfig factory ─────────────────────────────────────────────────────

    def to_llm_config(self, model_id: str | None = None) -> Any:
        """Return a LLMConfig for the given model_id (or active model)."""
        from .generator import LLMConfig

        m = self.get_active() if model_id is None else next(
            (x for x in self._data.get("models", []) if x["id"] == model_id), None
        )
        if m is None:
            return LLMConfig()  # safe default

        if m["type"] == "llama_cpp":
            return LLMConfig.llama_cpp(m.get("gguf_path", ""), m.get("n_gpu_layers", -1))
        return LLMConfig(
            model    = m["model"],
            base_url = m.get("base_url", "https://api.openai.com/v1"),
            api_key  = m.get("api_key", ""),
        )
