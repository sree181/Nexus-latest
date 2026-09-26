import { useQuery } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { api, type GovernanceEvidence } from "../lib/api";
import { ReviewEvidenceGraph } from "./ReviewEvidenceGraph";
import { StateFrame, WorkflowSection } from "./WorkflowVisual";

function short(value: string): string {
  return value.length > 22 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
}

export function GovernanceEvidencePanel({
  kind,
  id,
}: {
  kind: "policy" | "exception";
  id: string;
}) {
  const query = useQuery<GovernanceEvidence>({
    queryKey: ["governance-evidence", kind, id],
    queryFn: () => kind === "policy" ? api.policyEvidence(id) : api.exceptionEvidence(id),
    retry: false,
  });

  return (
    <WorkflowSection
      eyebrow="HyperMesh"
      title="Decision evidence"
      description="Recorded entities and native relationships only. No inferred links are added."
      action={<Button type="button" variant="ghost" onClick={() => void query.refetch()} disabled={query.isFetching}>{query.isFetching ? "Checking…" : "Refresh"}</Button>}
    >
      {query.isPending ? (
        <StateFrame kind="loading" title="Projecting decision evidence" detail="The workflow record is already available." />
      ) : query.isError ? (
        <StateFrame kind="error" title="Native evidence is not ready" detail="The workflow record remains available. Retry the evidence projection when the service is ready." action={<Button type="button" variant="ghost" onClick={() => void query.refetch()}>Try again</Button>} />
      ) : (
        <div className="governance-evidence-stack">
          <ReviewEvidenceGraph graph={query.data.graph} label={`${kind} decision evidence relationship map`} />
          <div className="governance-evidence-summary">
            <span><strong>{query.data.graph.relations.length}</strong><small>Relations</small></span>
            <span><strong>{query.data.graph.nodes.length}</strong><small>Entities</small></span>
            <span><strong>{query.data.projection_count}</strong><small>Acknowledged</small></span>
          </div>
          <details className="workflow-integrity">
            <summary>Integrity references</summary>
            <dl>
              {query.data.native_ulids.map((ulid, index) => (
                <div key={ulid}>
                  <dt>Native ULID</dt>
                  <dd title={ulid}>{short(ulid)}</dd>
                  <dt>Payload SHA-256</dt>
                  <dd title={query.data.payload_sha256[index]}>{short(query.data.payload_sha256[index] ?? "")}</dd>
                </div>
              ))}
            </dl>
          </details>
        </div>
      )}
    </WorkflowSection>
  );
}
