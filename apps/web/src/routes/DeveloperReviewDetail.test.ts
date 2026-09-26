import { describe, expect, it } from "vitest";

import type { ReviewRequest } from "../lib/api";
import { reviewNextStep } from "./DeveloperReviewDetail";

function review(overrides: Partial<ReviewRequest>): ReviewRequest {
  return {
    id: "rev_1",
    owner_subject: "maya",
    owner_name: "Maya",
    session_id: "ses_1",
    run_id: "run_1",
    repository_id: "repo_1",
    repository_name: "payments-api",
    policy_evaluation_id: "eval_1",
    package: "requests",
    version: "2.32.0",
    ecosystem: "PyPI",
    verdict: "block",
    severity: "high",
    advisories: [],
    code_entities: [],
    reasons: [],
    kind: "safe_version",
    rationale: "Need a safe release.",
    state: "waiting",
    analyst_subject: null,
    analyst_name: null,
    assignee: null,
    assignee_name: null,
    sla_due_at: null,
    overdue: false,
    escalated_case_id: null,
    decision_rationale: null,
    recommended_version: null,
    exception_expires_at: null,
    verification_evidence_id: null,
    evidence_digest: null,
    evidence_root_ulid: null,
    evidence_snapshot: {},
    version_counter: 1,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    priority: 50,
    priority_reasons: [],
    events: [],
    ...overrides,
  };
}

describe("developer review result guidance", () => {
  it("uses the recorded recommended version for requested changes", () => {
    expect(reviewNextStep(review({ state: "changes_requested", recommended_version: "2.33.0" })).title).toBe("Use 2.33.0");
  });

  it("keeps an escalated review read-only and pending", () => {
    expect(reviewNextStep(review({ state: "escalated" }))).toEqual(expect.objectContaining({ title: "Governance review continues", icon: "warning" }));
  });

  it("describes verification from the durable receipt", () => {
    expect(reviewNextStep(review({ state: "verified", verification_evidence_id: "eval:clean" })).detail).toContain("verification evidence");
  });
});
