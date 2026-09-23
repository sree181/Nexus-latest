"""
Project:     MeshAgent
File:        meshagent/llm.py
Description: The LLM adapter. One interface, LLMClient.step(), returns
             either tool calls to run or a final answer. MockLLM runs a
             scripted policy so the loop and its episode-writing are
             testable offline; AnthropicLLM binds the real model through
             the Anthropic SDK and OpenAILLM binds any OpenAI-compatible
             chat-completions endpoint (both lazy imports, so importing
             this module never requires an SDK or a key).
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .tools import ToolCall


@dataclass
class LLMStep:
    """One model turn: either it asks for tools, or it finalizes."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    final_text: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class LLMClient(Protocol):
    """The one method the loop needs."""

    def step(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMStep:
        ...


class ModelResponseError(RuntimeError):
    """The provider answered, but not with a usable chat completion."""


# ── mock ─────────────────────────────────────────────────────────────────

# A scripted policy: given the running message list, return the next
# LLMStep. Lets a test drive an exact sequence of tool calls then a final
# answer, deterministically and offline.
Policy = Callable[[list[dict[str, Any]]], LLMStep]


class MockLLM:
    """Drives the loop from a scripted policy or a fixed step sequence."""

    def __init__(
        self,
        *,
        policy: Policy | None = None,
        steps: list[LLMStep] | None = None,
    ) -> None:
        if policy is None and steps is None:
            raise ValueError("MockLLM needs a policy or a steps sequence")
        self._policy = policy
        self._steps = list(steps or [])
        self.calls = 0

    def step(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMStep:
        self.calls += 1
        if self._policy is not None:
            return self._policy(messages)
        if self._steps:
            return self._steps.pop(0)
        return LLMStep(final_text="(mock: no more scripted steps)")


# ── anthropic ────────────────────────────────────────────────────────────


class AnthropicLLM:
    """Binds Claude through the Anthropic SDK. Imported lazily; nothing in
    this module requires the SDK or an API key until you construct this."""

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-5",
        api_key: str | None = None,
        max_tokens: int = 4096,
        workspace_id: str | None = None,
        client: Any = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "AnthropicLLM needs the anthropic SDK: pip install anthropic"
                ) from exc
            # An organization-scoped key must name a workspace. The workspace
            # id is an identifier, not a secret, so it is safe to pass here or
            # via ANTHROPIC_WORKSPACE_ID; the api key still comes only from the
            # env or an explicit argument.
            import os

            ws = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID")
            headers = {"anthropic-workspace-id": ws} if ws else None
            self._client = anthropic.Anthropic(
                api_key=api_key, default_headers=headers
            )
        self._model = model
        self._max_tokens = max_tokens

    def step(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMStep:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,
            tools=tools or None,
        )
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=dict(block.input or {}),
                        call_id=block.id,
                    )
                )
            elif btype == "text":
                text_parts.append(block.text)
        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            }
        stop = getattr(resp, "stop_reason", None)
        if tool_calls and stop == "tool_use":
            return LLMStep(tool_calls=tool_calls, usage=usage)
        return LLMStep(final_text="".join(text_parts), usage=usage)


# ── openai-compatible ────────────────────────────────────────────────────


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the loop's Anthropic-shaped turns into chat-completions form.

    The loop speaks one message dialect (see loop._assistant_tool_turn), so
    the translation lives here rather than in the loop: adding a provider must
    not change how the loop records a turn."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue

        blocks = list(content or [])
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

        if uses:
            out.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [{
                    "id": b.get("id") or b["name"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}),
                    },
                } for b in uses],
            })
        # each tool result is its own message in this dialect
        for b in results:
            out.append({
                "role": "tool",
                "tool_call_id": b.get("tool_use_id") or "",
                "content": str(b.get("content", "")),
            })
        if text and not uses and not results:
            out.append({"role": msg["role"], "content": text})
    return out


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool.schema() is Anthropic-shaped; wrap it as a chat function."""
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        },
    } for t in tools]


class OpenAILLM:
    """Binds any OpenAI-compatible chat-completions endpoint.

    Imported lazily, like AnthropicLLM: nothing here needs the SDK or a key
    until you construct it. `base_url` is what makes this work against
    OpenAI itself, Azure-style gateways, vLLM, Ollama or OpenRouter."""

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        client: Any = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "OpenAILLM needs the openai SDK: pip install openai"
                ) from exc
            # the key still comes only from the env or an explicit argument
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens

    def step(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMStep:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "system", "content": system},
                         *_to_openai_messages(messages)],
        }
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        resp = self._client.chat.completions.create(**kwargs)
        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise ModelResponseError(
                f"model {self._model!r} returned no completion choices; "
                "verify that the configured endpoint supports this model"
            )
        choice = getattr(choices[0], "message", None)
        if choice is None:
            raise ModelResponseError(
                f"model {self._model!r} returned a completion without a message"
            )
        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {
                "input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
            }

        calls: list[ToolCall] = []
        for tc in getattr(choice, "tool_calls", None) or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                # a malformed call is still a call: let the tool layer reject
                # it and record the refusal, rather than crashing the run
                args = {"__malformed_arguments__": raw}
            calls.append(ToolCall(name=tc.function.name,
                                  arguments=args if isinstance(args, dict) else {},
                                  call_id=tc.id or tc.function.name))
        if calls:
            return LLMStep(tool_calls=calls, usage=usage)
        return LLMStep(final_text=choice.content or "", usage=usage)
