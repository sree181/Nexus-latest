import type { GraphPayload, NodeKind } from "./types";

/** Cytoscape element shape (typed locally so this package does not depend on
 *  cytoscape itself -- the web app owns that dependency). */
export interface CyElement {
  data: Record<string, unknown> & { id: string };
  classes?: string;
}

/** Map a MeshAgent payload to Cytoscape elements. Node `kind` and status flags
 *  become classes the stylesheet paints from (apps/web/features/graph). */
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
    classes: `rel-${e.rel}`,
  }));

  return [...nodes, ...edges];
}
