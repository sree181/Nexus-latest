import Graph from "graphology";
import type { GraphPayload } from "./types";

/** Build an in-memory graphology model from an API payload. This is the
 *  canonical model the app reasons over; renderers derive their own formats
 *  from it (see cytoscape.ts). */
export function toGraphology(payload: GraphPayload): Graph {
  const g = new Graph({ multi: true, type: "directed" });
  for (const n of payload.nodes) {
    if (!g.hasNode(n.id)) g.addNode(n.id, { ...n });
  }
  for (const e of payload.edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) {
      g.addEdgeWithKey(e.id, e.source, e.target, { rel: e.rel });
    }
  }
  return g;
}

/** Blast radius: everything reachable downstream of a node, one traversal.
 *  Mirrors MeshAgent's cve_impact / forget-closure on the client for preview. */
export function downstream(g: Graph, startId: string): Set<string> {
  const seen = new Set<string>();
  const stack = [startId];
  while (stack.length) {
    const cur = stack.pop()!;
    g.forEachOutNeighbor(cur, (nbr) => {
      if (!seen.has(nbr)) {
        seen.add(nbr);
        stack.push(nbr);
      }
    });
  }
  return seen;
}
