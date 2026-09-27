import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { api } from "../lib/api";
import { StateFrame, WorkflowHero, WorkflowJourney, WorkflowSection } from "../components/WorkflowVisual";

function Step({ index, title, detail, complete }: { index: number; title: string; detail: string; complete: boolean }) {
  return (
    <li className={`admin-onboarding-step ${complete ? "is-complete" : ""}`}>
      <span aria-hidden="true">{complete ? "✓" : index}</span>
      <div><strong>{title}</strong><p>{detail}</p></div>
    </li>
  );
}

export function AdminOnboarding() {
  const status = useQuery({
    queryKey: ["recorder-onboarding"],
    queryFn: api.recorderOnboarding,
    refetchInterval: 15_000,
  });

  if (status.isPending) return <main className="admin-page"><StateFrame kind="loading" title="Checking enterprise readiness…" /></main>;
  if (status.isError) return <main className="admin-page"><StateFrame kind="error" title="Readiness could not be verified" detail={status.error.message} action={<button type="button" onClick={() => void status.refetch()}>Try again</button>} /></main>;

  const data = status.data;
  const complete = data.identity_provider && data.signing_ready && data.active > 0;
  return (
    <main className="admin-page">
      <WorkflowHero
        eyebrow="Enterprise onboarding"
        title={complete ? "Recorder trust is active" : "Finish recorder trust setup"}
        tone={complete ? "success" : "accent"}
        description="Establish company identity, signed control-plane policy, and proof-bound recorder enrollment before broad deployment."
        status={<span className={`admin-status is-${complete ? "active" : "pending"}`}>{complete ? "Ready" : "Setup in progress"}</span>}
        meta={<span>{data.active} active · {data.attention} need attention · {data.legacy} legacy</span>}
      />

      <div className="admin-onboarding-grid">
        <WorkflowSection eyebrow="Readiness path" title="Three controls, one rollout gate">
          <ol className="admin-onboarding-steps">
            <Step index={1} title="Company identity" detail="Employees approve enrollment through the production OIDC session. Browser-selected roles are never accepted." complete={data.identity_provider} />
            <Step index={2} title="Control-plane signing" detail="Recorder policy snapshots are signed and verified locally before capture starts." complete={data.signing_ready} />
            <Step index={3} title="Enroll a recorder" detail="The recorder proves possession of its P-256 key and receives a ten-minute recording-only credential." complete={data.active > 0} />
          </ol>
        </WorkflowSection>

        <aside className="admin-command-panel" aria-label="Next administrator action">
          <p className="workflow-eyebrow">Next responsibility</p>
          <h2>{data.active > 0 ? "Review rollout health" : "Enroll the pilot device"}</h2>
          <p>{data.active > 0 ? "Confirm trust, delivery, and queue health before expanding deployment." : "Install the approved recorder build, then approve the code while signed in as the device owner."}</p>
          <Link to="/admin/recorders" className="admin-primary-link">Open recorder fleet</Link>
          <code>meshagent-recorder enroll --label "Maya laptop" --deployment "acme-prod"</code>
        </aside>
      </div>

      {data.legacy > 0 ? (
        <section className="admin-migration-notice" role="status">
          <div><strong>{data.legacy} legacy bearer recorder{data.legacy === 1 ? "" : "s"}</strong><span>Visible for migration; not silently upgraded to DPoP.</span></div>
          {data.legacy_retire_at ? <time dateTime={new Date(data.legacy_retire_at * 1000).toISOString()}>Retire by {new Date(data.legacy_retire_at * 1000).toLocaleDateString()}</time> : <span>Retirement date not configured</span>}
        </section>
      ) : null}

      <WorkflowSection eyebrow="Deployment sequence" title="What happens on each managed device">
        <WorkflowJourney items={[
          { id: "install", label: "Install", detail: "Native binary and per-user service", state: "complete" },
          { id: "approve", label: "Approve", detail: "Corporate user binds identity", state: data.active > 0 ? "complete" : "current" },
          { id: "verify", label: "Verify", detail: "DPoP and signed policy", state: data.active > 0 ? "complete" : "pending" },
          { id: "observe", label: "Observe", detail: "Content-free fleet health", state: data.active > 0 ? "current" : "pending" },
        ]} />
      </WorkflowSection>
    </main>
  );
}
