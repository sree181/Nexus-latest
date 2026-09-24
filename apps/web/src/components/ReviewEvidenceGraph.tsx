import type { GraphNode, GraphPayload } from "@meshagent/graph";

import { DevIcon, type DevIconName } from "./DeveloperIcons";

type Point = { x: number; y: number };

const kindIcon: Partial<Record<GraphNode["kind"], DevIconName>> = {
  agent: "user",
  source: "repository",
  class: "code",
  package: "package",
  version: "branch",
  cve: "warning",
  decision: "security",
  other: "session",
};

function layout(nodes: GraphNode[]): Map<string, Point> {
  const positions = new Map<string, Point>();
  const groups = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const values = groups.get(node.kind) ?? [];
    values.push(node);
    groups.set(node.kind, values);
  }
  const bases: Record<string, Point> = {
    agent: { x: 8, y: 18 },
    source: { x: 24, y: 18 },
    other: { x: 38, y: 38 },
    class: { x: 26, y: 72 },
    package: { x: 52, y: 58 },
    version: { x: 68, y: 58 },
    cve: { x: 86, y: 62 },
    decision: { x: 76, y: 18 },
  };
  for (const [kind, values] of groups) {
    const base = bases[kind] ?? { x: 50, y: 50 };
    values.forEach((node, index) => {
      const spread = Math.min(34, (values.length - 1) * 8);
      const offset = values.length === 1 ? 0 : -spread / 2 + index * (spread / (values.length - 1));
      const vertical = kind === "class" || kind === "cve";
      positions.set(node.id, {
        x: Math.max(7, Math.min(93, base.x + (vertical ? 0 : offset))),
        y: Math.max(10, Math.min(88, base.y + (vertical ? offset : 0))),
      });
    });
  }
  return positions;
}

function nodeTone(node: GraphNode): string {
  if (node.kind === "cve") return `review-node-${node.severity ?? "unknown"}`;
  if (node.kind === "decision") return "review-node-decision";
  if (node.kind === "package" || node.kind === "version") return "review-node-package";
  if (node.kind === "agent") return "review-node-person";
  return "review-node-neutral";
}

export function ReviewEvidenceGraph({ graph, label, selectedId, onSelect }: {
  graph: GraphPayload;
  label: string;
  selectedId?: string | null;
  onSelect?: (node: GraphNode) => void;
}) {
  const visibleNodes = graph.nodes.slice(0, 28);
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const positions = layout(visibleNodes);
  const edges = graph.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  return (
    <figure className="review-graph" aria-label={label}>
      <div className="review-graph-canvas">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" className="review-graph-lines">
          {edges.map((edge) => {
            const source = positions.get(edge.source);
            const target = positions.get(edge.target);
            if (!source || !target) return null;
            return <line key={edge.id} x1={source.x} y1={source.y} x2={target.x} y2={target.y} />;
          })}
        </svg>
        <div className="review-relation review-relation-context" aria-hidden="true" />
        <div className="review-relation review-relation-risk" aria-hidden="true" />
        {visibleNodes.map((node) => {
          const point = positions.get(node.id) ?? { x: 50, y: 50 };
          const detail = `${node.kind}. ${node.severity ? `${node.severity} severity. ` : ""}${node.label}`;
          return (
            <button
              type="button"
              key={node.id}
              className={`review-graph-node ${nodeTone(node)} ${selectedId === node.id ? "review-graph-node-selected" : ""}`}
              style={{ left: `${point.x}%`, top: `${point.y}%` }}
              aria-label={detail}
              aria-pressed={selectedId === node.id}
              title={detail}
              onClick={() => onSelect?.(node)}
            >
              <span className="review-graph-icon"><DevIcon name={kindIcon[node.kind] ?? "evidence"} size={16} /></span>
              <span>{node.label}</span>
            </button>
          );
        })}
      </div>
      <figcaption className="review-graph-legend">
        <span><i className="review-key review-key-context" />Contributor context</span>
        <span><i className="review-key review-key-risk" />Package and advisory</span>
        <span><i className="review-key review-key-decision" />Human decision</span>
      </figcaption>
    </figure>
  );
}
