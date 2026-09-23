"""
rag/generator.py — LLM Generation with Provenance Extraction

Calls an LLM (OpenAI API or compatible) with the assembled context,
then parses [HEDGE-N] citations from the response to build a provenance map.

Supports
--------
- OpenAI API (gpt-4o-mini, gpt-4o, gpt-3.5-turbo, any compatible endpoint)
- Any OpenAI-compatible server (Ollama, llama.cpp server, LM Studio)
  → set base_url to http://localhost:11434/v1 for Ollama
- llama-cpp-python in-process GGUF (set backend="llama_cpp")
  → fastest for edge / air-gapped deployments; no server needed
  → install: pip install llama-cpp-python
  → Metal (Apple Silicon): CMAKE_ARGS="-DLLAMA_METAL=on" pip install llama-cpp-python
- Streaming (async generator mode)

Provenance
----------
Every sentence citing [HEDGE-N] is tracked. Uncited claims are flagged
LOW_CONFIDENCE. The final response includes a sources list.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    model:       str   = "gpt-4o-mini"
    base_url:    str   = "https://api.openai.com/v1"
    api_key:     str   = ""
    temperature: float = 0.1      # low — we want factual, not creative
    max_tokens:  int   = 800
    timeout_s:   int   = 60
    # llama-cpp-python in-process backend
    backend:     str   = "openai"   # "openai" | "llama_cpp" | "mock" | "custom"
    gguf_path:   str   = ""         # path to .gguf file when backend="llama_cpp"
    n_gpu_layers: int  = -1         # -1 = all layers on GPU (Metal / CUDA)
    n_ctx:       int   = 4096
    # custom backend — bring-your-own model (Anthropic, Gemini, a fine-tune, …)
    chat_fn:     Any   = None       # callable(messages, cfg) -> str | dict | tuple

    @classmethod
    def mock(cls) -> "LLMConfig":
        """Deterministic, network-free backend. Produces a grounded answer that
        cites exactly the evidence/rule/step tags it was given — used for tests,
        CI, and air-gapped demos of the full pipeline without any model."""
        return cls(model="mock", backend="mock", api_key="mock")

    @classmethod
    def custom(cls, chat_fn: Any, model: str = "custom", **kwargs: Any) -> "LLMConfig":
        """Bring-your-own LLM. ``chat_fn`` may be sync or async and is called as
        ``chat_fn(messages, cfg)`` where ``messages`` is the OpenAI-style
        ``[{role, content}, ...]`` list. It must return one of:

        - ``str``                          — the answer text
        - ``{"text": str, "prompt_tokens": int, "completion_tokens": int,
              "model": str}``              — text + optional usage/model
        - ``(text, prompt_tokens, completion_tokens)``

        This is the integration point for any model that does not speak the
        OpenAI protocol (e.g. Anthropic Messages, Gemini, a local HF pipeline).
        Sync callables are run in a thread so they never block the event loop.
        """
        if not callable(chat_fn):
            raise TypeError("chat_fn must be callable")
        return cls(model=model, backend="custom", chat_fn=chat_fn, **kwargs)

    @classmethod
    def ollama(cls, model: str = "phi3:mini") -> "LLMConfig":
        return cls(model=model, base_url="http://localhost:11434/v1", api_key="ollama")

    @classmethod
    def lmstudio(cls, model: str = "local-model") -> "LLMConfig":
        return cls(model=model, base_url="http://localhost:1234/v1", api_key="lm-studio")

    @classmethod
    def llama_cpp(cls, gguf_path: str, n_gpu_layers: int = -1) -> "LLMConfig":
        """In-process llama-cpp-python — fastest local inference, no server needed."""
        return cls(
            model        = gguf_path,
            backend      = "llama_cpp",
            gguf_path    = gguf_path,
            n_gpu_layers = n_gpu_layers,
        )


# ── Provenance extraction ─────────────────────────────────────────────────────

_HEDGE_PATTERN = re.compile(r'\[HEDGE-(\d+)\]')
_SOURCES_PATTERN = re.compile(r'Sources:\s*((?:\[HEDGE-\d+\](?:,\s*)?)+)', re.I)


def _normalize_custom_result(result: Any) -> tuple[str, int, int, str | None]:
    """Coerce a custom ``chat_fn`` return into (text, prompt_tok, completion_tok, model)."""
    if isinstance(result, str):
        return result, 0, 0, None
    if isinstance(result, dict):
        return (
            str(result.get("text", result.get("content", ""))),
            int(result.get("prompt_tokens", 0) or 0),
            int(result.get("completion_tokens", 0) or 0),
            result.get("model"),
        )
    if isinstance(result, (tuple, list)):
        text = str(result[0]) if result else ""
        pt   = int(result[1]) if len(result) > 1 else 0
        ct   = int(result[2]) if len(result) > 2 else 0
        mdl  = result[3] if len(result) > 3 else None
        return text, pt, ct, mdl
    raise TypeError(
        "custom chat_fn must return str, dict, or tuple; got "
        f"{type(result).__name__}"
    )


def extract_citations(text: str) -> list[int]:
    """Return all HEDGE edge indices cited in the response text."""
    return [int(m.group(1)) for m in _HEDGE_PATTERN.finditer(text)]


def extract_sources_line(text: str) -> list[int]:
    """Extract the 'Sources: [HEDGE-N], ...' line."""
    m = _SOURCES_PATTERN.search(text)
    if m:
        return [int(x) for x in _HEDGE_PATTERN.findall(m.group(1))]
    return []


def score_confidence(answer: str, included_tags: list[str]) -> float:
    """
    Rough confidence score: fraction of sentences that have at least one citation.
    Returns 0.0–1.0.
    """
    sentences = [s.strip() for s in re.split(r'[.!?]', answer) if len(s.strip()) > 20]
    if not sentences:
        return 1.0
    cited = sum(1 for s in sentences if _HEDGE_PATTERN.search(s))
    return cited / len(sentences)


# ── LLMGenerator ─────────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    answer:         str
    cited_edge_ids: list[int]         = field(default_factory=list)
    confidence:     float             = 1.0
    model:          str               = ""
    prompt_tokens:  int               = 0
    completion_tokens: int            = 0
    elapsed_ms:     float             = 0.0
    low_confidence_warning: bool      = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer":           self.answer,
            "cited_edge_ids":   self.cited_edge_ids,
            "confidence":       round(self.confidence, 3),
            "model":            self.model,
            "prompt_tokens":    self.prompt_tokens,
            "completion_tokens":self.completion_tokens,
            "elapsed_ms":       round(self.elapsed_ms, 1),
            "low_confidence":   self.low_confidence_warning,
        }


class LLMGenerator:
    """
    Calls an LLM with system prompt + context + user query, then extracts citations.

    Parameters
    ----------
    config : LLMConfig with model, base_url, api_key, etc.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._cfg   = config
        self._client = None    # lazy-initialised OpenAI client
        self._llama  = None    # lazy-initialised llama-cpp-python model

    # ── Backend selection ─────────────────────────────────────────────────────

    def _get_client(self):
        """OpenAI-compatible async client (also used for Ollama / LM Studio)."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key  = self._cfg.api_key or "no-key",
                    base_url = self._cfg.base_url,
                    timeout  = self._cfg.timeout_s,
                )
            except ImportError as e:
                raise ImportError(
                    "openai package is required for LLM generation. "
                    "Install it with: pip install openai"
                ) from e
        return self._client

    def _get_llama(self):
        """Load a GGUF model via llama-cpp-python (in-process, no server)."""
        if self._llama is None:
            try:
                from llama_cpp import Llama
            except ImportError as e:
                raise ImportError(
                    "llama-cpp-python is required for local GGUF inference.\n"
                    "Install (CPU):         pip install llama-cpp-python\n"
                    "Install (Metal/M1-M4): CMAKE_ARGS='-DLLAMA_METAL=on' pip install llama-cpp-python\n"
                    "Install (CUDA):        CMAKE_ARGS='-DLLAMA_CUDA=on' pip install llama-cpp-python"
                ) from e

            import logging
            logging.getLogger("llama_cpp").setLevel(logging.WARNING)
            self._llama = Llama(
                model_path    = self._cfg.gguf_path,
                n_gpu_layers  = self._cfg.n_gpu_layers,
                n_ctx         = self._cfg.n_ctx,
                verbose       = False,
                chat_format   = "chatml",   # Phi-3 / Qwen / ChatML
            )
        return self._llama

    def _llama_chat(
        self,
        messages:    list[dict],
        max_tokens:  int,
        temperature: float,
    ) -> tuple[str, int, int]:
        """Synchronous llama-cpp call, returns (text, prompt_tokens, completion_tokens)."""
        llm = self._get_llama()
        response = llm.create_chat_completion(
            messages    = messages,
            max_tokens  = max_tokens,
            temperature = temperature,
        )
        text       = response["choices"][0]["message"]["content"] or ""
        usage      = response.get("usage", {})
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

    async def _call_custom(self, messages: list[dict]) -> tuple[str, int, int, str | None]:
        """Invoke a user-supplied chat_fn (sync or async), off the event loop if sync."""
        import asyncio
        import inspect

        fn = self._cfg.chat_fn
        if fn is None:
            raise ValueError("backend='custom' requires LLMConfig.chat_fn (use LLMConfig.custom(...))")
        if inspect.iscoroutinefunction(fn):
            result = await fn(messages, self._cfg)
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, fn, messages, self._cfg
            )
            if inspect.isawaitable(result):     # sync fn that returned a coroutine
                result = await result
        return _normalize_custom_result(result)

    @staticmethod
    def _mock_answer(user_query: str, included_tags: list[str]) -> str:
        """Deterministic grounded answer that cites every supplied tag. Designed
        to pass the hallucination firewall — it never asserts anything uncited."""
        tags = list(dict.fromkeys(included_tags))           # de-dup, keep order
        steps = [t for t in tags if t.startswith("STEP-")]
        rules = [t for t in tags if t.startswith("RULE-")]
        hedges = [t for t in tags if t.startswith("HEDGE-")]
        patterns = [t for t in tags if t.startswith("PATTERN-")]

        if not tags:
            return "[UNVERIFIED: cannot determine from available data]"

        lines = [f"Answer to: {user_query.strip()}", ""]
        if steps:
            for st in steps[:12]:
                cites = "".join(f"[{st}]" for st in [st])
                rcite = "".join(f"[{r}]" for r in rules[:1])
                lines.append(
                    f"A verified conclusion was derived by symbolic reasoning {cites}{rcite}."
                )
        for h in hedges[:8]:
            lines.append(f"This is supported by direct hypergraph evidence [{h}].")
        for p in patterns[:4]:
            lines.append(f"A structural pattern corroborates this finding [{p}].")

        sources = ", ".join(f"[{t}]" for t in tags[:24])
        lines.append("")
        lines.append(f"Sources: {sources}")
        return "\n".join(lines)

    async def generate(
        self,
        system_prompt: str,
        context:       str,
        user_query:    str,
        included_tags: list[str],
    ) -> GenerationResult:
        """
        Generate a grounded answer and extract provenance citations.
        """
        t0 = time.perf_counter()

        messages = [
            {"role": "system",    "content": system_prompt},
            {"role": "user",      "content": f"{context}\n\n---\n\nQuestion: {user_query}"},
        ]

        if self._cfg.backend == "mock":
            answer = self._mock_answer(user_query, included_tags)
            prompt_tokens = len(context) // 4
            completion_tokens = len(answer) // 4
            model_name = "mock"
        elif self._cfg.backend == "custom":
            answer, prompt_tokens, completion_tokens, custom_model = \
                await self._call_custom(messages)
            model_name = custom_model or self._cfg.model
        elif self._cfg.backend == "llama_cpp":
            # In-process GGUF — run in thread-pool to avoid blocking async loop
            import asyncio
            answer, prompt_tokens, completion_tokens = await asyncio.get_event_loop().run_in_executor(
                None, self._llama_chat, messages, self._cfg.max_tokens, self._cfg.temperature
            )
            model_name = f"llama_cpp:{self._cfg.gguf_path}"
        else:
            client   = self._get_client()
            response = await client.chat.completions.create(
                model       = self._cfg.model,
                messages    = messages,
                temperature = self._cfg.temperature,
                max_tokens  = self._cfg.max_tokens,
            )
            answer            = response.choices[0].message.content or ""
            usage             = response.usage
            prompt_tokens     = usage.prompt_tokens     if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0
            model_name        = response.model

        elapsed_ms = (time.perf_counter() - t0) * 1000
        cited_ids  = extract_citations(answer)
        sources    = extract_sources_line(answer)
        all_cited  = list(dict.fromkeys(cited_ids + sources))   # preserve order, dedup
        confidence = score_confidence(answer, included_tags)

        return GenerationResult(
            answer              = answer,
            cited_edge_ids      = all_cited,
            confidence          = confidence,
            model               = model_name,
            prompt_tokens       = prompt_tokens,
            completion_tokens   = completion_tokens,
            elapsed_ms          = elapsed_ms,
            low_confidence_warning = confidence < 0.5,
        )

    async def stream(
        self,
        system_prompt: str,
        context:       str,
        user_query:    str,
    ) -> AsyncIterator[str]:
        """Streaming version — yields token chunks (OpenAI/Ollama only; llama_cpp/mock fall back to blocking)."""
        if self._cfg.backend == "mock":
            yield self._mock_answer(user_query, [])
            return
        if self._cfg.backend == "custom":
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": f"{context}\n\n---\n\nQuestion: {user_query}"},
            ]
            answer, _, _, _ = await self._call_custom(messages)
            yield answer
            return
        if self._cfg.backend == "llama_cpp":
            # llama-cpp streaming is synchronous — yield full response at once
            import asyncio
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": f"{context}\n\n---\n\nQuestion: {user_query}"},
            ]
            answer, _, _ = await asyncio.get_event_loop().run_in_executor(
                None, self._llama_chat, messages, self._cfg.max_tokens, self._cfg.temperature
            )
            yield answer
            return

        client = self._get_client()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"{context}\n\n---\n\nQuestion: {user_query}"},
        ]

        stream = await client.chat.completions.create(
            model       = self._cfg.model,
            messages    = messages,
            temperature = self._cfg.temperature,
            max_tokens  = self._cfg.max_tokens,
            stream      = True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
