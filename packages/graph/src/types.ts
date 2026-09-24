/** The renderer-agnostic graph contract. The API returns this shape; every
 *  renderer (Cytoscape now, Sigma/deck.gl/three later) consumes it. Keeping
 *  one model in the middle is what makes the renderer swappable. */

export type NodeKind =
  | "source"
  | "decision"
  | "class"
  | "package"
  | "version"
  | "license"
  | "sink"
  | "cwe"
  | "cve"
  | "entry"
  | "agent"
  | "capability"
  | "module"
  | "function"
  | "api"
  | "policy"
  | "review"
  | "review_event"
  | "session"
  | "repository"
  | "other";

export type Plane = "provenance" | "security" | "supply" | "belief" | "time";

export type Severity = "critical" | "high" | "medium" | "low" | "unknown";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  /** planes this node participates in (for the multi-plane view) */
  planes?: Plane[];
  /** agent ids that reference this node (cross-fleet views) */
  owners?: string[];
  exploitable?: boolean;
  severity?: Severity;
  tombstoned?: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  rel: string; // derived | imports | calls | weakness | reaches | affects | has_version | licensed | uses
}

/** One memory hyperedge, as it was actually recorded.
 *
 *  Memory here is n-ary: a security finding is a single fact spanning
 *  {class, sink, capability, weakness}, written once, with one id and one
 *  provenance, and forgotten as a unit. The pairwise `edges` above cannot say
 *  that — they split that one fact into three lines and lose which lines
 *  belonged together. Relations carry the fact; edges stay the drawing. */
export interface Relation {
  /** the engine's ULID for the memory, so it can be traced and forgotten */
  id: string;
  /** source | decision | class | finding | version | cve | taint */
  kind: string;
  /** every entity the hyperedge spans, not just two */
  members: string[];
  label: string;
  tombstoned?: boolean;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Empty where a payload is assembled rather than recorded — the fleet
   *  query draws agents onto versions, which is a traversal, not a memory. */
  relations: Relation[];
}
