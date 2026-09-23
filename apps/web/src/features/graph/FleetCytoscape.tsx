import { useEffect, useRef } from "react";
import cytoscape, { type Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { toCytoscape, type GraphPayload } from "@meshagent/graph";

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

  return <div ref={ref} className="h-full w-full" role="img" aria-label="Cross-fleet hypergraph" />;
}
