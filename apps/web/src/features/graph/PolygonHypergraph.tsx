import { useMemo } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationNodeDatum,
} from "d3-force";
import { polygonCentroid, polygonHull } from "d3-polygon";
import type { HypergraphOut } from "../../lib/api";

/** The polygon visualization metaphor (Oliver, Zhang & Zhang, TVCG 2024):
 *  each hyperedge is drawn as a polygon enclosing its member vertices. We lay
 *  vertices out with a force simulation and draw a padded convex hull per
 *  hyperedge, colored by topological structure and (for blocks) by the
 *  entanglement index. This consumes only a HypergraphOut, so it sits behind
 *  the same data boundary as every other renderer. */

const W = 760;
const H = 560;
const PAD = 26;

type Node = SimulationNodeDatum & { id: string; kind: string; structure: string };

function layout(graph: HypergraphOut): Map<string, [number, number]> {
  const nodes: Node[] = graph.vertices.map((v) => ({ ...v }));
  const index = new Map(nodes.map((n) => [n.id, n]));
  const links: Array<{ source: string; target: string }> = [];
  for (const e of graph.edges) {
    const m = e.members.filter((x) => index.has(x));
    for (let i = 0; i < m.length; i++)
      for (let j = i + 1; j < m.length; j++) links.push({ source: m[i], target: m[j] });
  }
  const sim = forceSimulation(nodes)
    .force("charge", forceManyBody().strength(-240))
    .force("link", forceLink<Node, { source: string; target: string }>(links).id((d) => d.id).distance(74).strength(0.55))
    .force("center", forceCenter(W / 2, H / 2))
    .force("collide", forceCollide(26))
    .stop();
  for (let i = 0; i < 320; i++) sim.tick();
  return new Map(nodes.map((n) => [n.id, [n.x ?? W / 2, n.y ?? H / 2]]));
}

function edgeStyle(structure: string, etaByBlock: Record<string, number>) {
  if (structure.startsWith("block")) {
    const eta = etaByBlock[structure] ?? 0.2;
    return { fill: "#B3413C", opacity: 0.12 + Math.min(eta, 0.6) * 0.5, stroke: "#B3413C" };
  }
  if (structure.startsWith("bridge")) return { fill: "#0E7C8B", opacity: 0.12, stroke: "#0E7C8B" };
  return { fill: "#7E8A97", opacity: 0.09, stroke: "#B4C0CA" };
}

function padHull(points: [number, number][]): string {
  if (points.length < 3) return "";
  const hull = polygonHull(points);
  if (!hull) return "";
  const [cx, cy] = polygonCentroid(hull);
  const grown = hull.map(([x, y]) => {
    const dx = x - cx;
    const dy = y - cy;
    const len = Math.hypot(dx, dy) || 1;
    return [x + (dx / len) * PAD, y + (dy / len) * PAD] as [number, number];
  });
  return "M" + grown.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L ") + " Z";
}

export function PolygonHypergraph({
  graph,
  etaByBlock = {},
}: {
  graph: HypergraphOut;
  etaByBlock?: Record<string, number>;
}) {
  const pos = useMemo(() => layout(graph), [graph]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" role="img" aria-label="Hypergraph polygon view">
      {/* hyperedge polygons (painted first, behind vertices) */}
      {graph.edges.map((e) => {
        const pts = e.members.map((m) => pos.get(m)).filter(Boolean) as [number, number][];
        const st = edgeStyle(e.structure, etaByBlock);
        if (pts.length >= 3) {
          return (
            <path key={e.id} d={padHull(pts)} fill={st.fill} fillOpacity={st.opacity}
                  stroke={st.stroke} strokeOpacity={0.5} strokeWidth={1.5} strokeLinejoin="round" />
          );
        }
        if (pts.length === 2) {
          return (
            <line key={e.id} x1={pts[0][0]} y1={pts[0][1]} x2={pts[1][0]} y2={pts[1][1]}
                  stroke={st.fill} strokeOpacity={st.opacity + 0.15} strokeWidth={46} strokeLinecap="round" />
          );
        }
        return null;
      })}
      {/* vertices */}
      {graph.vertices.map((v) => {
        const p = pos.get(v.id);
        if (!p) return null;
        const label = v.id.includes(":") ? v.id.split(":").slice(1).join(":") : v.id;
        return (
          <g key={v.id}>
            <circle cx={p[0]} cy={p[1]} r={6} fill="#131A22" />
            <text x={p[0]} y={p[1] - 11} textAnchor="middle" fontFamily="JetBrains Mono, monospace"
                  fontSize={10} fill="#55606D">{label.length > 16 ? label.slice(0, 15) + "…" : label}</text>
          </g>
        );
      })}
    </svg>
  );
}
