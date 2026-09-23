"""
Project:     MeshAgent
File:        meshagent/tools.py
Description: The allowlisted toolset and its tiers. Tools are the only
             way the agent acts on the world, and each carries a tier
             that decides its approval path: READ tools run freely, WRITE
             tools are confined to the workspace and their effects are
             logged, DESTRUCTIVE tools require per-call confirmation. A
             tool not in the registry cannot be called, by construction.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable


class Tier(enum.IntEnum):
    """The approval tier a tool sits in."""

    READ = 1          # no side effects: auto-approved
    WRITE = 2         # workspace-scoped effects: allowed, logged
    DESTRUCTIVE = 3   # irreversible or external-effect: needs confirmation


class ToolError(RuntimeError):
    """A tool failed, was not found, or was denied."""


@dataclass
class ToolCall:
    """One requested invocation from the model."""

    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class ToolResult:
    """The outcome of dispatching one tool call, recorded in the episode."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    output: Any = None
    error: str | None = None
    tier: int = Tier.READ
    denied: bool = False

    def summary(self) -> dict[str, Any]:
        """The compact record that lands in the episode payload."""
        out = str(self.output)
        return {
            "tool": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "tier": int(self.tier),
            "denied": self.denied,
            "error": self.error,
            "output_preview": out[:280],
            "output_len": len(out),
        }


@dataclass
class Tool:
    """One allowlisted capability."""

    name: str
    tier: Tier
    description: str
    parameters: dict[str, Any]           # JSON-schema-style, for the model
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# Approval callback: given a ToolCall, return True to permit a
# DESTRUCTIVE action. Default denies, so an unattended run cannot take an
# irreversible action without an explicit policy.
ApprovalFn = Callable[[ToolCall], bool]


def deny_all(_call: ToolCall) -> bool:
    return False


class ToolRegistry:
    """The set of tools the agent may call. Absence is denial."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def dispatch(
        self, call: ToolCall, *, approve: ApprovalFn = deny_all
    ) -> ToolResult:
        """Run one tool call under its tier's policy. Never raises for a
        denied or failed call: the outcome is captured in the result so
        the loop records it in the episode and continues."""
        tool = self._tools.get(call.name)
        if tool is None:
            # Not allowlisted: hard stop for this call, recorded.
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                ok=False,
                error=f"tool {call.name!r} is not allowlisted",
                denied=True,
            )
        if tool.tier == Tier.DESTRUCTIVE and not approve(call):
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                ok=False,
                error="destructive action requires confirmation; denied",
                tier=int(tool.tier),
                denied=True,
            )
        try:
            output = tool.fn(**call.arguments)
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                ok=True,
                output=output,
                tier=int(tool.tier),
            )
        except Exception as exc:  # tools are effectful; contain their failures
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                tier=int(tool.tier),
            )


# ── the six default tools ────────────────────────────────────────────────
# Effectful tools (search, fetch, shell) are injected as callables so the
# loop stays testable offline and so deployment chooses the real
# implementations. The registry shape and tiers are fixed here.


@dataclass
class DefaultToolConfig:
    """Callables the six default tools wrap. Any left None is registered
    as a stub that fails cleanly rather than being silently absent."""

    web_search: Callable[..., Any] | None = None
    web_fetch: Callable[..., Any] | None = None
    file_read: Callable[..., Any] | None = None
    file_write: Callable[..., Any] | None = None
    shell: Callable[..., Any] | None = None
    memory_query: Callable[..., Any] | None = None
    extra: list[Tool] = field(default_factory=list)


def _unavailable(name: str) -> Callable[..., Any]:
    def _fn(**_kw: Any) -> Any:
        raise ToolError(f"{name} is not configured in this deployment")
    return _fn


def build_default_registry(cfg: DefaultToolConfig) -> ToolRegistry:
    """The 6-tool allowlist with fixed tiers."""
    tools = [
        Tool(
            "web_search", Tier.READ,
            "Search the web for a query string; returns ranked results.",
            {"type": "object",
             "properties": {"query": {"type": "string"}},
             "required": ["query"]},
            cfg.web_search or _unavailable("web_search"),
        ),
        Tool(
            "web_fetch", Tier.READ,
            "Fetch and return the readable text of a URL.",
            {"type": "object",
             "properties": {"url": {"type": "string"}},
             "required": ["url"]},
            cfg.web_fetch or _unavailable("web_fetch"),
        ),
        Tool(
            "file_read", Tier.READ,
            "Read a file inside the workspace.",
            {"type": "object",
             "properties": {"path": {"type": "string"}},
             "required": ["path"]},
            cfg.file_read or _unavailable("file_read"),
        ),
        Tool(
            "memory_query", Tier.READ,
            "Query agent memory for facts, episodes, or skills by subject.",
            {"type": "object",
             "properties": {"subject": {"type": "string"}},
             "required": ["subject"]},
            cfg.memory_query or _unavailable("memory_query"),
        ),
        Tool(
            "file_write", Tier.WRITE,
            "Write a file inside the workspace (workspace-scoped).",
            {"type": "object",
             "properties": {"path": {"type": "string"},
                            "content": {"type": "string"}},
             "required": ["path", "content"]},
            cfg.file_write or _unavailable("file_write"),
        ),
        Tool(
            "shell", Tier.DESTRUCTIVE,
            "Run a shell command in the sandbox. Requires confirmation.",
            {"type": "object",
             "properties": {"command": {"type": "string"}},
             "required": ["command"]},
            cfg.shell or _unavailable("shell"),
        ),
    ]
    reg = ToolRegistry(tools)
    for t in cfg.extra:
        reg.register(t)
    return reg
