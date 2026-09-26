import type { ReactNode } from "react";

import { ApiError } from "../lib/api";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const toneClasses: Record<Tone, string> = {
  neutral: "workflow-tone-neutral",
  accent: "workflow-tone-accent",
  success: "workflow-tone-success",
  warning: "workflow-tone-warning",
  danger: "workflow-tone-danger",
  info: "workflow-tone-info",
};

export function WorkflowHero({
  eyebrow,
  title,
  description,
  tone = "neutral",
  status,
  meta,
  children,
}: {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  tone?: Tone;
  status?: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className={`workflow-hero ${toneClasses[tone]}`}>
      <div className="workflow-hero-copy">
        <p className="workflow-eyebrow">{eyebrow}</p>
        <div className="workflow-hero-title-row">
          <h1>{title}</h1>
          {status}
        </div>
        {description ? <div className="workflow-hero-description">{description}</div> : null}
        {meta ? <div className="workflow-hero-meta">{meta}</div> : null}
      </div>
      {children ? <div className="workflow-hero-extra">{children}</div> : null}
    </section>
  );
}

export function WorkflowSection({
  eyebrow,
  title,
  description,
  action,
  children,
  className = "",
}: {
  eyebrow?: string;
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`workflow-section ${className}`}>
      {title || eyebrow || description || action ? (
        <header className="workflow-section-header">
          <div>
            {eyebrow ? <p className="workflow-eyebrow">{eyebrow}</p> : null}
            {title ? <h2>{title}</h2> : null}
            {description ? <div className="workflow-section-description">{description}</div> : null}
          </div>
          {action ? <div className="workflow-section-action">{action}</div> : null}
        </header>
      ) : null}
      <div className="workflow-section-body">{children}</div>
    </section>
  );
}

export function ResponsibilityDock({
  responsibility,
  why,
  label = "Your responsibility",
  deadline,
  overdue = false,
  children,
}: {
  responsibility: ReactNode;
  why?: ReactNode;
  label?: string;
  deadline?: ReactNode;
  overdue?: boolean;
  children?: ReactNode;
}) {
  return (
    <aside className="responsibility-dock" aria-label={label}>
      <div className="responsibility-dock-copy">
        <p className="workflow-eyebrow">{label}</p>
        <h2>{responsibility}</h2>
        {why ? <div className="responsibility-dock-why">{why}</div> : null}
      </div>
      {deadline ? (
        <div className={`responsibility-deadline ${overdue ? "is-overdue" : ""}`}>
          <span>{overdue ? "Overdue" : "Due"}</span>
          <strong>{deadline}</strong>
        </div>
      ) : null}
      {children ? <div className="responsibility-dock-actions">{children}</div> : null}
    </aside>
  );
}

export interface JourneyItem {
  id: string;
  label: string;
  detail?: string;
  state: "complete" | "current" | "pending" | "blocked";
}

export function WorkflowJourney({ items, label = "Workflow journey" }: { items: JourneyItem[]; label?: string }) {
  return (
    <ol className="workflow-journey" aria-label={label}>
      {items.map((item, index) => (
        <li key={item.id} className={`workflow-journey-item is-${item.state}`} aria-current={item.state === "current" ? "step" : undefined}>
          <span className="workflow-journey-marker" aria-hidden="true" />
          <span className="workflow-journey-copy">
            <strong>{item.label}</strong>
            {item.detail ? <small>{item.detail}</small> : null}
          </span>
          {index < items.length - 1 ? <span className="workflow-journey-line" aria-hidden="true" /> : null}
        </li>
      ))}
    </ol>
  );
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 412;
}

export function ConflictRecovery({
  error,
  onReload,
  draft,
}: {
  error: unknown;
  onReload: () => void;
  draft?: string;
}) {
  if (!isVersionConflict(error)) return null;
  return (
    <section className="workflow-conflict" role="alert">
      <div>
        <p className="workflow-eyebrow">Record changed</p>
        <h3>Review the latest version before submitting again.</h3>
        <p>Another authorized person updated this record. No change from this attempt was recorded.</p>
      </div>
      {draft ? (
        <details>
          <summary>Keep a copy of my rationale</summary>
          <p>{draft}</p>
        </details>
      ) : null}
      <button type="button" onClick={onReload}>Reload current record</button>
    </section>
  );
}

export function StateFrame({
  kind,
  title,
  detail,
  action,
}: {
  kind: "loading" | "empty" | "error" | "permission";
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <section className={`workflow-state is-${kind}`} role={kind === "error" || kind === "permission" ? "alert" : "status"}>
      <span className="workflow-state-symbol" aria-hidden="true">
        {kind === "loading" ? <span className="workflow-spinner" /> : kind === "empty" ? "·" : kind === "permission" ? "—" : "!"}
      </span>
      <h2>{title}</h2>
      {detail ? <p>{detail}</p> : null}
      {action ? <div>{action}</div> : null}
    </section>
  );
}

export function IntegrityRef({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  const readable = value.length > 30 ? `${value.slice(0, 12)}…${value.slice(-9)}` : value;
  return (
    <span className="workflow-integrity-ref" title={value}>
      <span>{label}</span>
      <code>{readable}</code>
    </span>
  );
}
