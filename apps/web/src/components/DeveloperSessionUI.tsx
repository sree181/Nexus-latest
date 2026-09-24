import type { ReactNode } from "react";
import { Badge } from "@meshagent/ui";

import type {
  ActivityEvent,
  DeveloperAdapter,
  DeveloperSessionStatus,
  PolicyEvaluation,
  ProjectionStatus,
} from "../lib/api";

export type BadgeTone = "neutral" | "ok" | "warn" | "risk" | "accent";

export function sessionStatusTone(status: DeveloperSessionStatus): BadgeTone {
  if (status === "completed") return "ok";
  if (status === "failed") return "risk";
  if (status === "ending") return "warn";
  return "accent";
}

export function projectionTone(status: ProjectionStatus): BadgeTone {
  if (status === "projected") return "ok";
  if (status === "failed" || status === "refused") return "risk";
  if (status === "projecting") return "accent";
  return "warn";
}

export function verdictTone(verdict: PolicyEvaluation["verdict"]): BadgeTone {
  if (verdict === "allow") return "ok";
  if (verdict === "block") return "risk";
  return "warn";
}

export function SessionStatusBadge({ status }: { status: DeveloperSessionStatus }) {
  return <Badge tone={sessionStatusTone(status)}>{status}</Badge>;
}

export function ProjectionBadge({ status }: { status: ProjectionStatus }) {
  return <Badge tone={projectionTone(status)}>{status}</Badge>;
}

export function AdapterBadge({ adapter }: { adapter: DeveloperAdapter }) {
  return (
    <Badge tone="neutral">
      {adapter === "claude-code" ? "Claude Code" : "Cursor"}
    </Badge>
  );
}

export function Metric({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-line bg-surface px-4 py-3">
      <p className="font-mono text-[10px] tracking-widest text-slate">{label.toUpperCase()}</p>
      <div className="mt-1.5 truncate font-serif text-[22px] font-semibold text-ink">{value}</div>
      {note ? <p className="mt-1 truncate text-xs text-slate">{note}</p> : null}
    </div>
  );
}

function text(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

export interface ActivityCopy {
  group: "session" | "prompt" | "tool" | "code" | "decision" | "package" | "security" | "response";
  label: string;
  title: string;
  detail: string;
}

export function activityCopy(event: Pick<ActivityEvent, "type" | "payload">): ActivityCopy {
  const p = event.payload;
  switch (event.type) {
    case "session.started":
      return { group: "session", label: "SESSION", title: "Session started", detail: text(p, "task") || "The editor connected to MeshAgent." };
    case "prompt.submitted":
      return { group: "prompt", label: "PROMPT", title: "Prompt submitted", detail: text(p, "prompt") || "Prompt content was recorded." };
    case "tool.started":
      return { group: "tool", label: "TOOL", title: `${text(p, "tool_name") || "Tool"} started`, detail: text(p, "detail") || "Execution started." };
    case "tool.completed":
      return { group: "tool", label: "TOOL", title: `${text(p, "tool_name") || "Tool"} completed`, detail: text(p, "detail") || "Execution completed." };
    case "tool.failed":
      return { group: "tool", label: "TOOL", title: `${text(p, "tool_name") || "Tool"} failed`, detail: text(p, "detail") || "Execution failed." };
    case "file.changed":
      return { group: "code", label: "CODE", title: `${text(p, "operation") || "updated"} ${text(p, "path") || "file"}`, detail: text(p, "because") || "Repository content changed." };
    case "decision.recorded":
      return { group: "decision", label: "DECISION", title: text(p, "decision_id") || "Decision recorded", detail: text(p, "statement") || "A governed decision was recorded." };
    case "package.requested":
      return { group: "package", label: "PACKAGE", title: `Requested ${text(p, "package") || "package"}${text(p, "version") ? `@${text(p, "version")}` : ""}`, detail: text(p, "command") || "A package operation was requested." };
    case "package.installed":
      return { group: "package", label: "PACKAGE", title: `Installed ${text(p, "package") || "package"}${text(p, "version") ? `@${text(p, "version")}` : ""}`, detail: text(p, "license") ? `License: ${text(p, "license")}` : "The package installation was observed." };
    case "policy.evaluated":
      return { group: "security", label: "POLICY", title: `${text(p, "package") || "Package"} ${text(p, "verdict") || "evaluated"}`, detail: text(p, "policy") || "Package policy was evaluated." };
    case "response.completed":
      return { group: "response", label: "RESPONSE", title: "Agent response completed", detail: text(p, "summary") || text(p, "stop_reason") || "The agent completed its response." };
    case "session.ended":
      return { group: "session", label: "SESSION", title: "Session ended", detail: text(p, "reason") || "The editor session ended." };
  }
}

export interface ProjectionSummary {
  projected: number;
  pending: number;
  attention: number;
  allProjected: boolean;
}

export function summarizeProjection(events: ActivityEvent[]): ProjectionSummary {
  const projected = events.filter((event) => event.projection_status === "projected").length;
  const pending = events.filter((event) =>
    event.projection_status === "pending" || event.projection_status === "projecting",
  ).length;
  const attention = events.filter((event) =>
    event.projection_status === "failed" || event.projection_status === "refused",
  ).length;
  return {
    projected,
    pending,
    attention,
    allProjected: events.length > 0 && projected === events.length,
  };
}

export function compactId(value: string, head = 8, tail = 5): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export const activityDot: Record<ActivityCopy["group"], string> = {
  session: "bg-slate",
  prompt: "bg-plane-belief",
  tool: "bg-accent",
  code: "bg-plane-provenance",
  decision: "bg-plane-belief",
  package: "bg-plane-supply",
  security: "bg-warn",
  response: "bg-ok",
};
