import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { sankey, sankeyLinkHorizontal } from 'd3-sankey';
import type { LinCard, LinColumn, LinNode } from '../liveView';
import './evidenceSankey.css';

type SankeyNodeDatum = {
  id: string;
  stageKey: string;
  stageLabel: string;
  stageIndex: number;
  order: number;
  card: LinCard;
  detail: LinNode;
  fixedValue?: number;
};

type SankeyLinkDatum = {
  source: string;
  target: string;
  value: number;
};

type EvidenceSankeyProps = {
  columns: LinColumn[];
  nodes: Record<string, LinNode>;
  edges: Array<[string, string]>;
  weights: Record<string, number>;
  selectedId: string | null;
  hotIds: string[];
  onSelect: (id: string) => void;
};

const STAGE_COLORS: Record<string, string> = {
  evidence: '#35d0a2',
  finding: '#65a8ff',
  incident: '#ff7478',
  recommendation: '#a78bfa',
  decision: '#f6a340',
  commitment: '#39c6b2',
  verification: '#73d7a4',
};

const AGENT_AVATARS: Record<string, string> = {
  atlas: '/avatars/madeleine-pitts.jpg',
  aqua: '/avatars/maxwell-tan.jpg',
  sentinel: '/avatars/marco-gross.jpg',
  phoenix: '/avatars/fergus-gray.jpg',
  forge: '/avatars/caitlyn-king.jpg',
  echo: '/avatars/courtney-turner.jpg',
};

const shortStage = (label: string) => label.replace(/\s+/g, ' ').trim();

export default function EvidenceSankey({
  columns,
  nodes,
  edges,
  weights,
  selectedId,
  hotIds,
  onSelect,
}: EvidenceSankeyProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const measure = () => {
      const rect = host.getBoundingClientRect();
      setSize({ width: Math.max(0, rect.width), height: Math.max(0, rect.height) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const hot = useMemo(() => new Set(hotIds), [hotIds]);
  const graph = useMemo(() => {
    if (size.width < 120 || size.height < 120) return null;
    const sankeyLinks: SankeyLinkDatum[] = edges
      .filter(([source, target]) => nodes[source] && nodes[target])
      .map(([source, target]) => ({
        source,
        target,
        value: Math.max(1, weights[`${source}>${target}`] || 1),
      }));
    const incoming = new Map<string, number>();
    const outgoing = new Map<string, number>();
    for (const link of sankeyLinks) {
      outgoing.set(link.source, (outgoing.get(link.source) || 0) + link.value);
      incoming.set(link.target, (incoming.get(link.target) || 0) + link.value);
    }
    const sankeyNodes: SankeyNodeDatum[] = columns.flatMap((column, stageIndex) =>
      column.cards.map((card, order) => ({
        id: card.id,
        stageKey: column.key,
        stageLabel: column.label,
        stageIndex: stageIndex + 1,
        order,
        card,
        detail: nodes[card.id],
        fixedValue: Math.max(1, incoming.get(card.id) || 0, outgoing.get(card.id) || 0),
      })),
    );

    const layout = sankey<SankeyNodeDatum, SankeyLinkDatum>()
      .nodeId(node => node.id)
      .nodeAlign(node => node.stageIndex - 1)
      .nodeSort((a, b) => a.order - b.order)
      .nodeWidth(12)
      .nodePadding(20)
      .extent([[28, 24], [size.width - 28, size.height - 24]])
      .iterations(64);

    return layout({ nodes: sankeyNodes.map(node => ({ ...node })), links: sankeyLinks.map(link => ({ ...link })) });
  }, [columns, edges, nodes, size.height, size.width, weights]);

  return (
    <section className="nx-sankey" aria-label="Evidence lineage Sankey diagram">
      <div className="nx-sankey__stages" aria-label="Lineage stages">
        {columns.map((column, index) => (
          <div className="nx-sankey__stage" key={column.key} style={{ '--stage-color': STAGE_COLORS[column.key] } as React.CSSProperties}>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <strong>{shortStage(column.label)}</strong>
            <small>{column.cards.length} {column.cards.length === 1 ? 'record' : 'records'}</small>
          </div>
        ))}
      </div>
      <div className="nx-sankey__canvas" ref={hostRef}>
        {graph ? (
          <svg width={size.width} height={size.height} role="img" aria-label="Citation flow from evidence through verification">
            <defs>
              {graph.links.map((link, index) => {
                const source = link.source as typeof graph.nodes[number];
                const target = link.target as typeof graph.nodes[number];
                return (
                  <linearGradient id={`nx-sankey-gradient-${index}`} key={index} gradientUnits="userSpaceOnUse" x1={source.x1} x2={target.x0}>
                    <stop offset="0%" stopColor={STAGE_COLORS[source.stageKey]} />
                    <stop offset="100%" stopColor={STAGE_COLORS[target.stageKey]} />
                  </linearGradient>
                );
              })}
            </defs>
            <g className="nx-sankey__links">
              {graph.links.map((link, index) => {
                const source = link.source as typeof graph.nodes[number];
                const target = link.target as typeof graph.nodes[number];
                const active = !selectedId || (hot.has(source.id) && hot.has(target.id));
                return (
                  <path
                    className={active ? 'is-active' : 'is-dimmed'}
                    data-sankey-link={`${source.id}>${target.id}`}
                    d={sankeyLinkHorizontal()(link) || undefined}
                    key={`${source.id}-${target.id}-${index}`}
                    fill="none"
                    stroke={`url(#nx-sankey-gradient-${index})`}
                    strokeWidth={Math.max(3, link.width || 1)}
                  />
                );
              })}
            </g>
            <g className="nx-sankey__nodes">
              {graph.nodes.map(node => {
                const selected = selectedId === node.id;
                const related = !selectedId || hot.has(node.id);
                const x0 = node.x0 ?? 0;
                const x1 = node.x1 ?? x0 + 12;
                const y0 = node.y0 ?? 0;
                const y1 = node.y1 ?? y0 + 8;
                const stageWidth = Math.max(158, size.width / Math.max(1, columns.length) - 26);
                const lateStage = node.stageIndex >= columns.length - 1;
                let labelX = lateStage ? Math.max(8, x0 - stageWidth - 14) : x1 + 14;
                let labelY = Math.max(4, y0 - 4);
                if (node.stageKey === 'decision') {
                  labelX = Math.max(8, x0 - stageWidth / 2);
                  labelY = Math.max(8, Math.min(size.height - 112, (y0 + y1) / 2 - 50));
                }
                const labelHeight = Math.min(108, Math.max(64, y1 - y0 + 42));
                const agentCode = node.id.startsWith('f-') ? node.id.slice(2).toLowerCase() : '';
                const agentAvatar = AGENT_AVATARS[agentCode];
                return (
                  <g className={`nx-sankey__node${selected ? ' is-selected' : ''}${related ? '' : ' is-dimmed'}`} key={node.id}>
                    <rect
                      x={x0}
                      y={y0}
                      width={Math.max(4, x1 - x0)}
                      height={Math.max(8, y1 - y0)}
                      rx="3"
                      fill={STAGE_COLORS[node.stageKey]}
                    />
                    <foreignObject x={labelX} y={labelY} width={stageWidth} height={labelHeight}>
                      <button
                        type="button"
                        className="nx-sankey-card"
                        data-sankey-node={node.id}
                        data-stage={node.stageKey}
                        aria-pressed={selected}
                        onClick={() => onSelect(node.id)}
                        style={{ '--node-color': STAGE_COLORS[node.stageKey] } as React.CSSProperties}
                      >
                        {agentAvatar ? (
                          <span className="nx-sankey-card__identity">
                            <img src={agentAvatar} alt="" />
                            <span className="nx-sankey-card__kicker" style={{ color: STAGE_COLORS[node.stageKey] }}>{node.card.kicker}</span>
                          </span>
                        ) : (
                          <span className="nx-sankey-card__kicker" style={{ color: STAGE_COLORS[node.stageKey] }}>{node.card.kicker}</span>
                        )}
                        <strong>{node.card.title}</strong>
                        <small>{node.detail?.src || node.card.meta || node.stageLabel}</small>
                      </button>
                    </foreignObject>
                  </g>
                );
              })}
            </g>
          </svg>
        ) : (
          <div className="nx-sankey__loading">Laying out the record flow…</div>
        )}
      </div>
    </section>
  );
}
