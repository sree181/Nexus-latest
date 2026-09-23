import { useEffect, useRef } from "react";
import cytoscape, { type Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { relationRows, toCytoscape, type GraphPayload } from "@meshagent/graph";

cytoscape.use(fcose);

/** Real WebGL-ish graph rendering via Cytoscape. Node `kind`/status become
 *  classes the stylesheet paints. Swap this file for a Sigma/deck.gl/three
 *  renderer later without touching the rest of the app -- it only consumes a
 *  GraphPayload. */
const stylesheet: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      "background-color": "#3A7CA5",
      label: "data(label)",
      color: "#131A22",
      "font-family": "JetBrains Mono, monospace",
      "font-size": 11,
      "text-valign": "bottom",
      "text-margin-y": 6,
      width: 30,
      height: 30,
    },
  },
  { selector: "node.kind-version", style: { "background-color": "#0E7C8B", width: 54, height: 54, color: "#FFFFFF", "text-valign": "center", "text-margin-y": 0, "font-size": 10 } },
  { selector: "node.kind-agent", style: { "background-color": "#EAEEF2", "border-width": 1.5, "border-color": "#B4C0CA", color: "#2A5E7E" } },
  { selector: "node.kind-source", style: { "background-color": "#6B74C9" } },
  { selector: "node.kind-decision", style: { "background-color": "#0E7C8B" } },
  { selector: "node.kind-class", style: { "background-color": "#1F7A4D" } },
  { selector: "node.kind-sink", style: { "background-color": "#B3413C" } },
  { selector: "node.kind-relation", style: { "shape": "round-rectangle", "background-color": "#6B74C9", width: 22, height: 22, "font-size": 9, "text-valign": "center", "text-margin-y": 0, color: "#FFFFFF" } },
  { selector: "node.exploitable", style: { "background-color": "#B3413C", "border-width": 3, "border-color": "#7d2724" } },
  { selector: "node.tombstoned", style: { opacity: 0.35 } },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#CBD3DB",
      "target-arrow-color": "#CBD3DB",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "arrow-scale": 0.8,
    },
  },
  { selector: "edge.rel-reaches", style: { "line-color": "#B3413C", "target-arrow-color": "#B3413C", width: 2.5, "line-style": "dashed" } },
  { selector: "edge.rel-calls", style: { "line-color": "#B3413C", "target-arrow-color": "#B3413C" } },
  { selector: "edge.relation-member", style: { "line-color": "#6B74C9", "target-arrow-shape": "none", "line-style": "dotted", width: 1.5 } },
];

export function FleetCytoscape({
  payload,
  onSelect,
}: {
  payload: GraphPayload;
  onSelect?: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const relations = relationRows(payload);

  useEffect(() => {
    if (!ref.current) return;
    const cy = cytoscape({
      container: ref.current,
      elements: toCytoscape(payload),
      style: stylesheet,
      layout: { name: "fcose", animate: false, quality: "default", nodeSeparation: 90 } as never,
      minZoom: 0.3,
      maxZoom: 2.5,
    });
    cy.on("tap", "node", (e) => onSelect?.(e.target.id()));
    cyRef.current = cy;

    // Cytoscape caches the container's size at construction, so a graph laid
    // out while the panel was narrow stays fitted to that width forever.
    const observer = new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 30);
    });
    observer.observe(ref.current);

    return () => {
      observer.disconnect();
      cy.destroy();
    };
  }, [payload, onSelect]);

  return (
    <div className="relative h-full w-full">
      <div
        ref={ref}
        className="h-full w-full"
        role="img"
        aria-label="Cross-fleet graph. Round rectangles are recorded n-ary facts; dotted lines show their members."
        aria-describedby="fleet-graph-alternative"
      />
      <details
        id="fleet-graph-alternative"
        className="absolute bottom-3 right-3 max-h-[70%] max-w-[calc(100%-1.5rem)] overflow-auto rounded-lg border border-line bg-surface p-2 text-left shadow-[var(--shadow)]"
      >
        <summary className="cursor-pointer px-1 font-mono text-[11px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Text and table alternative
        </summary>
        <div className="flex min-w-[300px] flex-col gap-3 p-2">
          <p className="text-[11.5px] leading-snug text-slate">
            Direct links are traversals. Recorded n-ary facts are represented by
            one relation row with all of their members; they are not pairwise
            claims.
          </p>
          <table className="w-full border-collapse text-left text-[11px]">
            <caption className="mb-1 text-left font-mono text-[10px] tracking-wide text-slate">NODES</caption>
            <thead><tr className="border-b border-line"><th scope="col">Label</th><th scope="col">Kind</th></tr></thead>
            <tbody>{payload.nodes.map((node) => <tr key={node.id} className="border-b border-line"><td className="py-1 pr-3 text-ink">{node.label}</td><td className="py-1 font-mono text-slate">{node.kind}</td></tr>)}</tbody>
          </table>
          <table className="w-full border-collapse text-left text-[11px]">
            <caption className="mb-1 text-left font-mono text-[10px] tracking-wide text-slate">DIRECT LINKS</caption>
            <thead><tr className="border-b border-line"><th scope="col">From</th><th scope="col">Relation</th><th scope="col">To</th></tr></thead>
            <tbody>{payload.edges.map((edge) => <tr key={edge.id} className="border-b border-line"><td className="py-1 pr-2 text-ink">{payload.nodes.find((node) => node.id === edge.source)?.label ?? edge.source}</td><td className="py-1 pr-2 font-mono text-slate">{edge.rel}</td><td className="py-1 text-ink">{payload.nodes.find((node) => node.id === edge.target)?.label ?? edge.target}</td></tr>)}</tbody>
          </table>
          {relations.length > 0 && (
            <table className="w-full border-collapse text-left text-[11px]">
              <caption className="mb-1 text-left font-mono text-[10px] tracking-wide text-slate">RECORDED N-ARY FACTS</caption>
              <thead><tr className="border-b border-line"><th scope="col">Fact</th><th scope="col">Kind</th><th scope="col">Members</th></tr></thead>
              <tbody>{relations.map((relation) => <tr key={relation.id} className="border-b border-line"><td className="py-1 pr-2 text-ink">{relation.label}</td><td className="py-1 pr-2 font-mono text-slate">{relation.kind}</td><td className="py-1 text-ink">{relation.members.join(", ")}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </details>
    </div>
  );
}
