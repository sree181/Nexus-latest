"""
Project:     MeshAgent
File:        meshagent/__init__.py
Description: MeshAgent, the lightweight governed agent whose memory and
             state live in the HyperMesh hypergraph via hypermeshdb.
             agentmem. Phase 4: the loop, the allowlisted toolset with
             tiers, the LLM adapter (mock + Anthropic), and per-turn
             episode writing with full run reconstruction.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from .codegraph import (
    CodeGraphRecorder,
    ExtractedClass,
    export_graph,
    extract_code_entities,
)
from .demo_codegraph import build as build_codegraph_demo
from .osv import Advisory, OSVClient, advisories_from_osv, join_osv
from .sarif import (
    ScanIngest,
    TaintFinding,
    ingest_sarif,
    ingest_scan,
    sarif_findings,
    scanned_modules,
    scan_tool,
)
from .demos import (
    PoisonDemo,
    PoisonTranscript,
    UnlearnDemo,
    UnlearnTranscript,
)
from .llm import AnthropicLLM, LLMClient, LLMStep, MockLLM
from .loop import AgentLoop, TaskResult
from .reconstruct import ReconstructedRun, reconstruct
from .scheduler import RunReport, Runner, Schedule, Scheduler
from .skills import (
    SkillManager,
    SkillRecord,
    SkillState,
    signature_of,
)
from .subagent import Handoff, Orchestrator, Subagent
from .tools import (
    ApprovalFn,
    DefaultToolConfig,
    Tier,
    Tool,
    ToolCall,
    ToolError,
    ToolRegistry,
    ToolResult,
    build_default_registry,
    deny_all,
)

__all__ = [
    "AgentLoop",
    "AnthropicLLM",
    "ApprovalFn",
    "DefaultToolConfig",
    "LLMClient",
    "LLMStep",
    "CodeGraphRecorder",
    "ExtractedClass",
    "Handoff",
    "Advisory",
    "OSVClient",
    "advisories_from_osv",
    "join_osv",
    "TaintFinding",
    "ScanIngest",
    "ingest_sarif",
    "ingest_scan",
    "sarif_findings",
    "scanned_modules",
    "scan_tool",
    "build_codegraph_demo",
    "export_graph",
    "extract_code_entities",
    "MockLLM",
    "Orchestrator",
    "PoisonDemo",
    "PoisonTranscript",
    "ReconstructedRun",
    "RunReport",
    "Runner",
    "Schedule",
    "Scheduler",
    "SkillManager",
    "SkillRecord",
    "SkillState",
    "Subagent",
    "TaskResult",
    "UnlearnDemo",
    "UnlearnTranscript",
    "signature_of",
    "Tier",
    "Tool",
    "ToolCall",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
    "deny_all",
    "reconstruct",
]
