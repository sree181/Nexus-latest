import { useMemo, useState } from "react";
import { polygonHull } from "d3-polygon";
import { relationRows, type GraphNode, type GraphPayload, type Relation } from "@meshagent/graph";

import { DevIcon, type DevIconName } from "./DeveloperIcons";

type Point = { x: number; y: number };

const kindIcon: Partial<Record<GraphNode["kind"], DevIconName>> = {
  agent: "user",
  source: "repository",
  class: "code",
  function: "code",
  module: "file",
  package: "package",
  version: "branch",
  cve: "warning",
  cwe: "warning",
  decision: "security",
  policy: "security",
  policy_version: "security",
  review: "evidence",
  review_event: "activity",
  governance_event: "activity",
  exception: "lock",
  session: "session",
  repository: "repository",
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
    agent: { x: 10, y: 18 },
    source: { x: 19, y: 30 },
    session: { x: 12, y: 58 },
    repository: { x: 27, y: 78 },
    class: { x: 40, y: 72 },
    function: { x: 43, y: 82 },
    module: { x: 34, y: 52 },
    package: { x: 50, y: 47 },
    version: { x: 60, y: 66 },
    cve: { x: 72, y: 66 },
    cwe: { x: 78, y: 80 },
    policy: { x: 72, y: 27 },
    policy_version: { x: 61, y: 17 },
    review: { x: 86, y: 42 },
    review_event: { x: 88, y: 63 },
    governance_event: { x: 84, y: 73 },
    exception: { x: 91, y: 25 },
    decision: { x: 87, y: 14 },
    other: { x: 50, y: 20 },
  };
  const ordered = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  ordered.forEach(([kind, values], groupIndex) => {
    const base = bases[kind] ?? { x: 22 + ((groupIndex * 17) % 66), y: 24 + ((groupIndex * 23) % 58) };
    const columns = Math.max(1, Math.ceil(Math.sqrt(values.length)));
    values.forEach((node, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const xOffset = (column - (columns - 1) / 2) * 12;
      const yOffset = (row - (Math.ceil(values.length / columns) - 1) / 2) * 14;
      positions.set(node.id, {
        x: Math.max(7, Math.min(93, base.x + xOffset)),
        y: Math.max(10, Math.min(89, base.y + yOffset)),
      });
    });
  });
  return positions;
}

function nodeTone(node: GraphNode): string {
  if (node.kind === "cve" || node.kind === "cwe") return `review-node-${node.severity ?? "unknown"}`;
  if (node.kind === "decision" || node.kind === "exception") return "review-node-decision";
  if (node.kind === "package" || node.kind === "version") return "review-node-package";
  if (node.kind === "agent") return "review-node-person";
  return "review-node-neutral";
}

function relationPoints(relation: Relation, positions: Map<string, Point>): Array<[number, number]> {
  const points = relation.members.map((member) => positions.get(member)).filter((point): point is Point => Boolean(point));
  const padded = points.flatMap((point) => [
    [point.x - 5, point.y - 8] as [number, number],
    [point.x + 5, point.y - 8] as [number, number],
    [point.x + 5, point.y + 8] as [number, number],
    [point.x - 5, point.y + 8] as [number, number],
  ]);
  return polygonHull(padded) ?? padded;
}

export function ReviewEvidenceGraph({ graph, label, selectedId, onSelect }: {
  graph: GraphPayload;
  label: string;
  selectedId?: string | null;
  onSelect?: (node: GraphNode) => void;
}) {
  const [selectedRelation, setSelectedRelation] = useState<string | null>(null);
  const visibleNodes = graph.nodes.slice(0, 28);
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const positions = useMemo(() => layout(visibleNodes), [visibleNodes]);
  const edges = graph.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  const relations = graph.relations;
  const visualRelations = relations.filter((relation) => relation.members.every((member) => visibleIds.has(member)));
  const rows = relationRows(graph);
  const selectedMembers = new Set(relations.find((relation) => relation.id === selectedRelation)?.members ?? []);

  return (
    <figure className="evidence-field" aria-labelledby={`${label.replace(/\W+/g, "-").toLowerCase()}-caption`}>
      <div className="evidence-field-heading">
        <div>
          <p className="workflow-eyebrow">Native HyperMesh evidence</p>
          <p>{graph.relations.length} recorded relationship{graph.relations.length === 1 ? "" : "s"} · {graph.nodes.length} entit{graph.nodes.length === 1 ? "y" : "ies"}</p>
        </div>
        {graph.nodes.length > visibleNodes.length ? <span>{visualRelations.length} complete relations mapped · full trail below</span> : null}
      </div>
      <div className="review-graph-canvas evidence-field-canvas">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" className="review-graph-lines">
          {visualRelations.map((relation, index) => {
            const points = relationPoints(relation, positions);
            if (points.length < 3) return null;
            const active = !selectedRelation || selectedRelation === relation.id;
            return (
              <polygon
                key={relation.id}
                points={points.map(([x, y]) => `${x},${y}`).join(" ")}
                className={`evidence-relation-hull evidence-relation-hull-${index % 4}`}
                opacity={active ? 1 : 0.12}
              />
            );
          })}
          {edges.map((edge) => {
            const source = positions.get(edge.source);
            const target = positions.get(edge.target);
            if (!source || !target) return null;
            const active = !selectedRelation || selectedMembers.has(edge.source) || selectedMembers.has(edge.target);
            return <line key={edge.id} x1={source.x} y1={source.y} x2={target.x} y2={target.y} opacity={active ? 0.75 : 0.12} />;
          })}
        </svg>
        {visibleNodes.map((node) => {
          const point = positions.get(node.id) ?? { x: 50, y: 50 };
          const detail = `${node.kind}. ${node.severity ? `${node.severity} severity. ` : ""}${node.label}`;
          const dimmed = selectedRelation ? !selectedMembers.has(node.id) : false;
          return (
            <button
              type="button"
              key={node.id}
              className={`review-graph-node ${nodeTone(node)} ${selectedId === node.id ? "review-graph-node-selected" : ""}`}
              style={{ left: `${point.x}%`, top: `${point.y}%`, opacity: dimmed ? 0.18 : 1 }}
              aria-label={detail}
              aria-pressed={selectedId === node.id}
              title={detail}
              onClick={() => {
                setSelectedRelation(null);
                onSelect?.(node);
              }}
            >
              <span className="review-graph-icon"><DevIcon name={kindIcon[node.kind] ?? "evidence"} size={16} /></span>
              <span>{node.label}</span>
            </button>
          );
        })}
        {visibleNodes.length === 0 ? <p className="evidence-field-empty">No entities were projected for this record.</p> : null}
      </div>

      <figcaption id={`${label.replace(/\W+/g, "-").toLowerCase()}-caption`} className="evidence-field-caption">
        <div className="evidence-field-caption-copy">
          <strong>{label}</strong>
          <span>Select a recorded relation below to focus its visible entities. The text trail remains complete.</span>
        </div>
        {rows.length ? (
          <ol className="evidence-relation-list" aria-label="Recorded evidence relationships">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className={selectedRelation === row.id ? "is-selected" : ""}
                  aria-pressed={selectedRelation === row.id}
                  onClick={() => setSelectedRelation((current) => current === row.id ? null : row.id)}
                >
                  <span className="evidence-relation-kind">{row.kind.replaceAll("_", " ")}</span>
                  <span className="evidence-relation-copy">
                    <strong>{row.label}</strong>
                    <small>{row.members.join(" · ")}</small>
                  </span>
                  {row.tombstoned ? <span className="evidence-relation-state">Forgotten</span> : !visualRelations.some((relation) => relation.id === row.id) ? <span className="evidence-relation-state">Text only</span> : null}
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="evidence-no-relations">This projection contains drawing links but no native n-ary relations.</p>
        )}
      </figcaption>
    </figure>
  );
}
