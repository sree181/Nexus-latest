"""
Project:     MeshAgent
File:        meshagent/build_tools.py
Description: The toolset for a governed build. Both tools write into the
             code graph through CodeGraphRecorder, so the only way the model
             can act is by writing memory that carries provenance.
             record_code runs the real AST scan, which means the classes,
             the packages imported and the dangerous call sites that appear
             afterwards are extracted from code the model actually wrote --
             not described by it. The bill of materials is taken from those
             imports rather than from the model's account of them.
             Nothing here records a taint path: reachability needs evidence
             this toolset cannot produce, so findings stay `present` and
             exploitability is left unclaimed.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from .codegraph import CodeGraphRecorder, extract_code_entities
from .tools import Tier, Tool, ToolRegistry

SYSTEM = (
    "You are MeshAgent, a governed autonomous coding agent. You act only "
    "through the provided tools, and every tool call writes a memory record "
    "that carries its provenance.\n\n"
    "Work in this order:\n"
    "1. record_decision -- state the approach you are taking, once, in one "
    "sentence, before writing any code.\n"
    "2. write_code -- the actual Python for the task, as one module. Write "
    "real, working code: it is parsed, and the classes it defines and the "
    "packages it imports become governed memory. Define at least one class.\n"
    "Then give a one-paragraph final answer describing what you built.\n\n"
    "The packages you import are read from your code, so import exactly what "
    "you use. Do not state versions or advisories: the platform resolves "
    "those from real feeds. Do not claim anything is secure or unreachable."
)


def third_party(packages: list[str]) -> list[str]:
    """The imported packages that are not part of the standard library.

    Only these can carry a version or an advisory, so only these belong in
    the bill of materials."""
    std = sys.stdlib_module_names
    return [p for p in dict.fromkeys(packages) if p and p not in std]


@dataclass
class BuildLog:
    """What the build produced, for the caller to act on and disclose.

    The memory itself is written by the recorder and narrates itself from its
    own payloads, so nothing here describes the graph. This carries only what
    the graph cannot say: the code that was submitted, the third-party imports
    to resolve against feeds, and any gap worth telling the user about."""

    code: str = ""
    packages: list[str] = field(default_factory=list)
    final_text: str | None = None
    notices: list[str] = field(default_factory=list)

    def note(self, detail: str) -> None:
        self.notices.append(detail)


def build_registry(
    recorder: CodeGraphRecorder,
    log: BuildLog,
    *,
    from_sources: list[str],
    decision_id: str = "approach",
) -> ToolRegistry:
    """The tools a governed build may use, bound to one recorder.

    `from_sources` are the recorder source ids the decision rests on -- for a
    run, the task the user stated."""
    state: dict[str, Any] = {"decided": False, "modules": 0}

    def record_decision(statement: str) -> str:
        if state["decided"]:
            return ("A decision is already recorded for this run; continue "
                    "with write_code.")
        text = (statement or "").strip()
        if not text:
            raise ValueError("statement must say what the approach is")
        recorder.record_decision(decision_id, text, from_sources=from_sources)
        state["decided"] = True
        return "Recorded the decision, derived from this run's task."

    def write_code(code: str) -> str:
        if not state["decided"]:
            raise ValueError("call record_decision before write_code")
        # parse before recording, so a syntax error is reported to the model
        # as a tool failure it can correct rather than a half-written graph
        entities = extract_code_entities(code)
        if not entities:
            raise ValueError(
                "no class was found: write a module that defines at least "
                "one class, so the code graph has something to govern")

        # a model may submit more than once; each submission is its own module
        # so neither the text nor the class listing is overwritten
        state["modules"] += 1
        module = "main.py" if state["modules"] == 1 else f"main_{state['modules']}.py"
        recorder.record_code(code, decision_id=decision_id, module=module)
        log.code = f"{log.code}\n\n{code}".strip() if log.code else code
        names = [e.name for e in entities]
        log.packages = third_party([p for e in entities for p in e.packages])
        sinks = sum(len(e.sinks) for e in entities)
        return (
            f"Recorded {len(names)} class(es) and the packages they import. "
            f"The AST scan found {sinks} dangerous call site(s)."
        )

    return ToolRegistry([
        Tool(
            name="record_decision", tier=Tier.WRITE,
            description=("State the approach for this task in one sentence. "
                         "Call this once, before writing code."),
            parameters={
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "The approach, in one sentence.",
                    },
                },
                "required": ["statement"],
            },
            fn=record_decision,
        ),
        Tool(
            name="write_code", tier=Tier.WRITE,
            description=("Submit the Python module for this task. It is "
                         "parsed: the classes it defines and the packages it "
                         "imports become governed memory. Define at least one "
                         "class."),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "A complete Python module.",
                    },
                },
                "required": ["code"],
            },
            fn=write_code,
        ),
    ])
