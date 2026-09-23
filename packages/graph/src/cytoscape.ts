import type { GraphPayload, NodeKind } from "./types";

/** Cytoscape element shape (typed locally so this package does not depend on
 *  cytoscape itself -- the web app owns that dependency). */
export interface CyElement {
  data: Record<string, unknown> & { id: string };
  classes?: string;
}

export interface RelationRow {
  id: string;
  kind: string;
  label: string;
  members: string[];
  tombstoned: boolean;
}

/** A text/table-friendly view of recorded n-ary facts. Member ids become their
 * current node labels when possible, so an assistive alternative says what the
 * graph means rather than exposing renderer-specific ids. */
export function relationRows(payload: GraphPayload): RelationRow[] {
  const labels = new Map(payload.nodes.map((node) => [node.id, node.label]));
  return payload.relations.map((relation) => ({
    id: relation.id,
    kind: relation.kind,
    label: relation.label,
    members: relation.members.map((member) => labels.get(member) ?? member),
    tombstoned: Boolean(relation.tombstoned),
  }));
}

/** Map a MeshAgent payload to Cytoscape elements. Recorded relations are
 * represented by an explicit relation node with one membership edge per member;
 * this preserves an n-ary fact's single identity and avoids claiming its
 * members are independently pairwise related. Ordinary `edges` remain direct
 * traversal links and are styled separately by the web renderer. */
export function toCytoscape(payload: GraphPayload): CyElement[] {
  const nodes: CyElement[] = payload.nodes.map((n) => ({
    data: {
      id: n.id,
      label: n.label,
      kind: n.kind,
      owners: n.owners ?? [],
      severity: n.severity ?? null,
    },
    classes: [
      `kind-${n.kind as NodeKind}`,
      n.exploitable ? "exploitable" : "",
      n.tombstoned ? "tombstoned" : "",
    ]
      .filter(Boolean)
      .join(" "),
  }));

  const edges: CyElement[] = payload.edges.map((e) => ({
    data: { id: e.id, source: e.source, target: e.target, rel: e.rel },
    classes: `graph-link rel-${e.rel}`,
  }));

  const knownNodes = new Set(payload.nodes.map((node) => node.id));
  const relationNodes: CyElement[] = [];
  const membershipEdges: CyElement[] = [];
  for (const relation of payload.relations) {
    const relationNodeId = `relation:${relation.id}`;
    relationNodes.push({
      data: {
        id: relationNodeId,
        label: relation.label || relation.kind,
        kind: "relation",
        relationId: relation.id,
        relationKind: relation.kind,
        memberCount: relation.members.length,
      },
      classes: ["kind-relation", relation.tombstoned ? "tombstoned" : ""]
        .filter(Boolean)
        .join(" "),
    });
    relation.members.forEach((member, index) => {
      if (!knownNodes.has(member)) return;
      membershipEdges.push({
        data: {
          id: `relation-member:${relation.id}:${index}`,
          source: relationNodeId,
          target: member,
          rel: "member",
          relationId: relation.id,
        },
        classes: "relation-member",
      });
    });
  }

  return [...nodes, ...relationNodes, ...edges, ...membershipEdges];
}
