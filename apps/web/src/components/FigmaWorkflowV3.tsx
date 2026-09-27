import { useId, useMemo, useState, type ReactNode } from "react";
import { relationRows, type GraphNode, type GraphPayload, type NodeKind } from "@meshagent/graph";

export type FigmaJourneyStage =
  | "detected"
  | "developer_action"
  | "security_review"
  | "investigation"
  | "ciso_decision"
  | "remediation"
  | "verified";

type StageState = "complete" | "current" | "pending" | "blocked";

const journeyStages: Array<{ id: FigmaJourneyStage; label: string }> = [
  { id: "detected", label: "Detected" },
  { id: "developer_action", label: "Dev action" },
  { id: "security_review", label: "Security review" },
  { id: "investigation", label: "Investigation" },
  { id: "ciso_decision", label: "CISO decision" },
  { id: "remediation", label: "Remediation" },
  { id: "verified", label: "Verified" },
];

export function FigmaJourney({
  current,
  overrides,
  vertical = false,
  label = "Workflow journey",
}: {
  current: FigmaJourneyStage;
  overrides?: Partial<Record<FigmaJourneyStage, StageState>>;
  vertical?: boolean;
  label?: string;
}) {
  const currentIndex = journeyStages.findIndex((stage) => stage.id === current);
  return (
    <ol className={`figma3-journey ${vertical ? "is-vertical" : ""}`} aria-label={label}>
      {journeyStages.map((stage, index) => {
        const state = overrides?.[stage.id] ?? (index < currentIndex ? "complete" : index === currentIndex ? "current" : "pending");
        return (
          <li key={stage.id} className={`is-${state}`} aria-current={state === "current" ? "step" : undefined}>
            <span className="figma3-journey-dot" aria-hidden="true" />
            <span className="figma3-journey-label">{stage.label}</span>
            {index < journeyStages.length - 1 ? <span className="figma3-journey-line" aria-hidden="true" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

type EvidenceLens = "why" | "affected" | "outcome";

const lensCopy: Record<EvidenceLens, string> = {
  why: "Why was this blocked?",
  affected: "What code is affected?",
  outcome: "What changes the outcome?",
};

const lensPriority: Record<EvidenceLens, NodeKind[]> = {
  why: ["session", "source", "package", "version", "cve", "cwe", "policy", "policy_version", "review", "decision", "exception", "repository", "module", "function", "other"],
  affected: ["package", "version", "module", "class", "function", "repository", "source", "session", "policy", "cve", "review", "decision", "other"],
  outcome: ["version", "package", "cve", "policy", "policy_version", "review", "decision", "exception", "governance_event", "repository", "session", "other"],
};

const nodeTone: Partial<Record<NodeKind, string>> = {
  session: "blue",
  source: "blue",
  repository: "blue",
  package: "slate",
  version: "green",
  cve: "red",
  cwe: "red",
  policy: "amber",
  policy_version: "amber",
  review: "teal",
  review_event: "teal",
  decision: "teal",
  exception: "amber",
  governance_event: "violet",
  module: "violet",
  class: "violet",
  function: "violet",
};

function shorten(value: string, max = 19): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function selectNodes(graph: GraphPayload, lens: EvidenceLens, maxNodes: number): GraphNode[] {
  const rank = new Map(lensPriority[lens].map((kind, index) => [kind, index]));
  return [...graph.nodes]
    .filter((node) => !node.tombstoned)
    .sort((left, right) => {
      const leftRank = rank.get(left.kind) ?? 100;
      const rightRank = rank.get(right.kind) ?? 100;
      if (leftRank !== rightRank) return leftRank - rightRank;
      return left.label.localeCompare(right.label);
    })
    .slice(0, maxNodes);
}

export function FigmaEvidenceGraph({
  graph,
  label,
  selectedId,
  onSelect,
  height = 200,
  mobileEvidenceOnly = false,
}: {
  graph: GraphPayload;
  label: string;
  selectedId?: string | null;
  onSelect?: (node: GraphNode) => void;
  height?: number;
  mobileEvidenceOnly?: boolean;
}) {
  const [lens, setLens] = useState<EvidenceLens>("why");
  const markerId = useId().replaceAll(":", "");
  const nodes = useMemo(() => selectNodes(graph, lens, 6), [graph, lens]);
  const nodeIds = useMemo(() => new Set(nodes.map((node) => node.id)), [nodes]);
  const rows = useMemo(() => relationRows(graph), [graph]);
  const positions = useMemo(() => {
    const result = new Map<string, { x: number; y: number }>();
    const count = Math.max(nodes.length, 1);
    nodes.forEach((node, index) => {
      const x = count === 1 ? 430 : 72 + (716 * index) / (count - 1);
      const y = count < 5 ? 120 : index % 2 === 0 ? 94 : 146;
      result.set(node.id, { x, y });
    });
    return result;
  }, [nodes]);
  const directEdges = graph.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  const relationEdges = graph.relations.flatMap((relation) => {
    const members = relation.members.filter((member) => nodeIds.has(member));
    return members.slice(1).map((member, index) => ({
      id: `${relation.id}-${index}`,
      source: members[index],
      target: member,
      rel: relation.kind,
      relation: true,
    }));
  });
  const edges = directEdges.length ? directEdges.map((edge) => ({ ...edge, relation: false })) : relationEdges;
  const visibleRows = rows.slice(0, 3);

  return (
    <figure className={`figma3-evidence ${mobileEvidenceOnly ? "is-evidence-only" : ""}`}>
      <div className="figma3-lenses" role="tablist" aria-label="Evidence question">
        <span>Evidence question</span>
        <div>
          {(Object.entries(lensCopy) as Array<[EvidenceLens, string]>).map(([value, copy]) => (
            <button key={value} type="button" role="tab" aria-selected={lens === value} onClick={() => setLens(value)}>{copy}</button>
          ))}
        </div>
      </div>
      {!mobileEvidenceOnly ? (
        <div className="figma3-graph-canvas" style={{ height }}>
          <svg viewBox="0 0 860 240" preserveAspectRatio="xMidYMid meet" role="img" aria-label={`${label}: ${lensCopy[lens]}`}>
            <defs>
              <marker id={`${markerId}-arrow`} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L0,7 L7,3.5 z" />
              </marker>
            </defs>
            <rect x="0" y="0" width="860" height="240" className="figma3-graph-bg" />
            {[60, 120, 180].map((y) => <line key={y} x1="0" y1={y} x2="860" y2={y} className="figma3-graph-grid" />)}
            {edges.map((edge) => {
              const source = positions.get(edge.source);
              const target = positions.get(edge.target);
              if (!source || !target) return null;
              const x1 = source.x + 58;
              const x2 = target.x - 58;
              const midX = (x1 + x2) / 2;
              const midY = (source.y + target.y) / 2 - (Math.abs(source.y - target.y) > 20 ? 18 : 0);
              const path = `M ${x1} ${source.y} Q ${midX} ${midY} ${x2} ${target.y}`;
              return (
                <g key={edge.id}>
                  <path d={path} className={edge.relation ? "figma3-graph-edge is-relation" : "figma3-graph-edge"} markerEnd={`url(#${markerId}-arrow)`} />
                  <text x={midX} y={midY - 7} textAnchor="middle" className="figma3-graph-edge-label">{shorten(edge.rel.replaceAll("_", " "), 17)}</text>
                </g>
              );
            })}
            {nodes.map((node) => {
              const point = positions.get(node.id) ?? { x: 430, y: 120 };
              const selected = selectedId === node.id;
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected}
                  aria-label={`${node.kind}: ${node.label}`}
                  className={`figma3-graph-node is-${nodeTone[node.kind] ?? "slate"} ${selected ? "is-selected" : ""}`}
                  onClick={() => onSelect?.(node)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelect?.(node);
                  }}
                >
                  {selected ? <rect x={point.x - 64} y={point.y - 27} width="128" height="54" rx="10" className="figma3-graph-node-focus" /> : null}
                  <rect x={point.x - 60} y={point.y - 23} width="120" height="46" rx="7" />
                  <text x={point.x} y={point.y - 2} textAnchor="middle" className="figma3-graph-node-label">{shorten(node.label)}</text>
                  <text x={point.x} y={point.y + 13} textAnchor="middle" className="figma3-graph-node-kind">{node.kind.replaceAll("_", " ")}</text>
                </g>
              );
            })}
          </svg>
          {!nodes.length ? <p className="figma3-graph-empty">No entities were projected for this record.</p> : null}
        </div>
      ) : null}
      <figcaption className="figma3-evidence-trail">
        <div className="figma3-evidence-trail-heading"><strong>{label}</strong><span>{graph.relations.length} relationships · {graph.nodes.length} entities</span></div>
        {visibleRows.length ? (
          <ol>
            {visibleRows.map((row) => (
              <li key={row.id}>
                <span className={row.tombstoned ? "is-tombstoned" : ""} aria-hidden="true" />
                <code>{shorten(row.id, 18)}</code>
                <span className="figma3-evidence-members"><strong>{row.label}</strong><small>{row.members.join(" · ")}</small></span>
              </li>
            ))}
          </ol>
        ) : <p>No native relationships were returned for this projection.</p>}
        {rows.length > visibleRows.length ? (
          <details className="figma3-complete-trail">
            <summary>Complete evidence trail ({rows.length})</summary>
            <ol>
              {rows.map((row) => <li key={row.id}><code>{row.kind.replaceAll("_", " ")}</code><span><strong>{row.label}</strong><small>{row.members.join(" · ")}</small></span></li>)}
            </ol>
          </details>
        ) : null}
      </figcaption>
    </figure>
  );
}

export function FigmaResponsibilityDock({
  responsibility,
  why,
  actions,
  deadline,
  overdue = false,
  blockingCondition,
  children,
  label = "Your responsibility",
  className = "",
}: {
  responsibility: ReactNode;
  why: ReactNode;
  actions?: ReactNode;
  deadline?: ReactNode;
  overdue?: boolean;
  blockingCondition?: ReactNode;
  children?: ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <aside className={`figma3-dock ${className}`} aria-label={label}>
      <div className="figma3-dock-intro"><p>{label}</p><h2>{responsibility}</h2><div>{why}</div></div>
      {actions ? <div className="figma3-dock-actions">{actions}</div> : null}
      {deadline || blockingCondition ? (
        <div className={`figma3-dock-deadline ${overdue ? "is-overdue" : ""}`}>
          <small>{overdue ? "Overdue" : deadline ? "Deadline" : "Blocking condition"}</small>
          {deadline ? <strong>{deadline}</strong> : null}
          {blockingCondition ? <span>{blockingCondition}</span> : null}
        </div>
      ) : null}
      {children ? <div className="figma3-dock-extra">{children}</div> : null}
    </aside>
  );
}

export function FigmaMobileDock({ children, deadline }: { children: ReactNode; deadline?: ReactNode }) {
  return (
    <div className="figma3-mobile-dock">
      {deadline ? <div className="figma3-mobile-deadline">Due {deadline}</div> : null}
      <div>{children}</div>
    </div>
  );
}
