import { describe, expect, it } from "vitest";

import {
  activityCopy,
  summarizeProjection,
} from "../components/DeveloperSessionUI";
import { projectsFromSessions, sessionsForProject } from "../components/DeveloperProject";
import type { ActivityEvent, DeveloperSession, PolicyEvaluation } from "../lib/api";
import { filterActivity } from "./DeveloperSessionActivity";
import { editorHook, repositoryConfig } from "./DeveloperConnectionsEditors";
import { filterDeveloperSessions } from "./DeveloperSessions";
import { summarizeSecurity } from "./DeveloperSessionSecurity";

function session(
  id: string,
  status: DeveloperSession["status"],
  runId: string | null,
): DeveloperSession {
  return {
    id,
    owner_subject: "maya@company.com",
    owner_name: "Maya",
    adapter: "cursor",
    adapter_version: "1",
    source_session_id: `source-${id}`,
    repository: { id: "repo-test", name: "test", remote: null, branch: null, commit: null },
    task: "Test the Developer workspace",
    status,
    started_at_ms: 1,
    last_seen_at_ms: 2,
    ended_at_ms: status === "completed" ? 3 : null,
    next_sequence: 3,
    last_acked_sequence: 2,
    run_id: runId,
    device_id: "device-1",
    verified: true,
    failure_reason: null,
  };
}

function event(
  type: ActivityEvent["type"],
  projection: ActivityEvent["projection_status"],
  payload: Record<string, unknown> = {},
): ActivityEvent {
  return {
    event_id: `evt_${type.replaceAll(".", "_")}_0123456789abcdef`,
    source_event_id: `source-${type}`,
    session_id: "ses_0123456789abcdef",
    sequence: 2,
    type,
    occurred_at_ms: 1,
    received_at_ms: 2,
    payload,
    payload_sha256: "abc",
    projection_status: projection,
    projection_attempts: 1,
    projection_last_attempt_at_ms: 2,
    projection_next_attempt_at_ms: null,
    projected_at_ms: projection === "projected" ? 2 : null,
    projection_error: null,
    run_id: "run-1",
  };
}

function policy(verdict: PolicyEvaluation["verdict"], unavailable: string | null = null): PolicyEvaluation {
  return {
    id: `policy-${verdict}`,
    session_id: "ses_0123456789abcdef",
    activity_event_id: `event-${verdict}`,
    package: "httpx",
    version: "0.27.2",
    verdict,
    reasons: [],
    advisories: [],
    worst: null,
    unavailable,
    policy: "block high severity advisories",
    evaluated_at_ms: 1,
    owner_subject: "maya@company.com",
    device_id: "device-1",
  };
}

describe("Developer Sessions workspace", () => {
  it("keeps live, completed, and attention filters operationally distinct", () => {
    const sessions = [
      session("active", "active", "run-1"),
      session("complete", "completed", "run-2"),
      session("failed", "failed", null),
      session("unprojected", "active", null),
    ];

    expect(filterDeveloperSessions(sessions, "live").map((item) => item.id)).toEqual([
      "active",
      "unprojected",
    ]);
    expect(filterDeveloperSessions(sessions, "completed").map((item) => item.id)).toEqual([
      "complete",
    ]);
    expect(filterDeveloperSessions(sessions, "attention").map((item) => item.id)).toEqual([
      "failed",
      "unprojected",
    ]);
  });

  it("separates ledger delivery from governed projection health", () => {
    const summary = summarizeProjection([
      event("session.started", "projected"),
      event("file.changed", "pending"),
      event("tool.failed", "failed"),
      event("policy.evaluated", "refused"),
    ]);
    expect(summary).toEqual({ projected: 1, pending: 1, attention: 2, allProjected: false });
  });

  it("turns wire events into concise activity language without changing evidence", () => {
    expect(
      activityCopy(
        event("file.changed", "projected", {
          path: "src/payments.py",
          operation: "update",
          because: "retry-policy",
        }),
      ),
    ).toMatchObject({
      group: "code",
      label: "CODE",
      title: "update src/payments.py",
      detail: "retry-policy",
    });
  });

  it("counts policy outcomes and feed degradation independently", () => {
    expect(
      summarizeSecurity([
        policy("allow"),
        policy("warn"),
        policy("block"),
        policy("unknown", "OSV unavailable"),
      ]),
    ).toEqual({ allowed: 1, warnings: 1, blocked: 1, unknown: 1, unavailable: 1 });
  });

  it("derives Project scope from stable repository identity rather than branches", () => {
    const first = session("first", "completed", "run-1");
    first.repository = { id: "repo-alpha", name: "Alpha", remote: "git://alpha", branch: "main", commit: "a" };
    const second = session("second", "active", "run-2");
    second.repository = { id: "repo-alpha", name: "Alpha", remote: "git://alpha", branch: "feature", commit: "b" };
    const third = session("third", "completed", "run-3");
    third.repository = { id: "repo-beta", name: "Beta", remote: null, branch: "main", commit: "c" };
    expect(projectsFromSessions([third, second, first]).map((item) => item.id)).toEqual(["repo-alpha", "repo-beta"]);
    expect(sessionsForProject([first, second, third], "repo-alpha").map((item) => item.id)).toEqual(["first", "second"]);
  });

  it("filters dense activity by semantics and attention without changing order", () => {
    const file = event("file.changed", "projected", { path: "src/app.py", operation: "update", code: "x" });
    const failed = event("tool.failed", "failed", { tool_name: "pytest", detail: "failed" });
    failed.sequence = 3;
    expect(filterActivity([file, failed], "code", "app.py")).toEqual([file]);
    expect(filterActivity([file, failed], "attention", "")).toEqual([failed]);
  });

  it("generates explicit opt-in and editor hook configuration", () => {
    expect(JSON.parse(repositoryConfig(true, ["*.env"]))).toEqual({ record: true, gate: true, exclude: ["*.env"] });
    expect(editorHook("cursor", "/opt/meshagent")).toContain("/opt/meshagent/adapters/cursor/meshagent_hook.py");
    expect(editorHook("claude", "/opt/meshagent")).toContain("/opt/meshagent/adapters/claude-code/meshagent_hook.py");
  });
});
