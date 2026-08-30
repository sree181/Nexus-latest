import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { graphApi } from '../graphApi';
import type { GraphEdge, GraphNode, GraphSnapshot, GraphView } from '../graphTypes';
import type { OperationalEvent } from '../operationalTypes';
import '../graphWorkspace.css';

const viewLabels: Record<GraphView, { title: string; subtitle: string }> = {
  decision_lineage: { title: 'Decision Lineage', subtitle: 'Current and historical path from evidence to verification' },
  agency_coordination: { title: 'Agency Coordination', subtitle: 'Who was asked, who decided, and what remains open' },
  mobility: { title: 'Mobility Flow', subtitle: 'Roads, lots, transit, closures, and corridors from ingested batches' },
};

const viewOrder: GraphView[] = ['decision_lineage', 'agency_coordination', 'mobility'];
const lineageOrder = ['evidence', 'finding', 'incident', 'recommendation', 'decision', 'commitment', 'verification'];

function title(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function GraphEmpty({ view }: { view: GraphView }) {
  const copy = view === 'decision_lineage'
    ? 'No recommendation has been composed for this window yet. The chain appears here when a named rule opens an incident.'
    : view === 'agency_coordination'
      ? 'No agency handoffs yet. Commitments appear after a human records a decision.'
      : 'Authoritative mobility batches will appear here after an approved source publishes nodes for this window.';
  return <div className="graph-empty"><strong>No {viewLabels[view].title.toLowerCase()} yet</strong><p>{copy}</p></div>;
}

function MobilityFlow({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (node: GraphNode) => void }) {
  const groups = useMemo(() => Object.entries(nodes.reduce<Record<string, GraphNode[]>>((result, node) => {
    (result[node.nodeType] ??= []).push(node);
    return result;
  }, {})), [nodes]);
  if (!nodes.length) return <GraphEmpty view="mobility" />;
  return <div className="mobility-flow">
    <div className="mobility-flow__metrics">
      <span><strong>{nodes.length}</strong> current assets</span>
      <span><strong>{edges.length}</strong> active relationships</span>
      <span><strong>{nodes.filter(node => node.qualityFlags.includes('stale')).length}</strong> stale assets</span>
    </div>
    <div className="mobility-flow__lanes">
      {groups.map(([type, items]) => <section key={type} className="graph-lane"><header>{title(type)} <small>{items?.length}</small></header><div>
        {(items ?? []).map(node => <button key={node.nodeId} className="graph-node-card" onClick={() => onSelect(node)}>
          <span className={`classification classification--${node.dataClassification}`}>{title(node.dataClassification)}</span>
          <strong>{node.label}</strong><small>v{node.version} · {new Date(node.updatedAt).toLocaleTimeString()}</small>
        </button>)}
      </div></section>)}
    </div>
  </div>;
}

function DecisionLineage({
  nodes, edges, selected, onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: GraphNode | null;
  onSelect: (node: GraphNode) => void;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [paths, setPaths] = useState<Array<{ id: string; d: string; active: boolean }>>([]);

  const relatedIds = useMemo(() => {
    if (!selected) return null;
    const ids = new Set<string>([selected.nodeId]);
    for (const edge of edges) {
      if (edge.fromNodeId === selected.nodeId) ids.add(edge.toNodeId);
      if (edge.toNodeId === selected.nodeId) ids.add(edge.fromNodeId);
    }
    return ids;
  }, [edges, selected]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    setPaths(edges.flatMap(edge => {
      const from = nodeRefs.current.get(edge.fromNodeId)?.getBoundingClientRect();
      const to = nodeRefs.current.get(edge.toNodeId)?.getBoundingClientRect();
      if (!from || !to) return [];
      const x1 = from.right - box.left;
      const y1 = from.top + from.height / 2 - box.top;
      const x2 = to.left - box.left;
      const y2 = to.top + to.height / 2 - box.top;
      const mid = (x1 + x2) / 2;
      const active = !relatedIds || (relatedIds.has(edge.fromNodeId) && relatedIds.has(edge.toNodeId));
      return [{ id: edge.edgeId, d: `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`, active }];
    }));
  }, [edges, nodes, relatedIds, selected]);

  if (!nodes.length) return <GraphEmpty view="decision_lineage" />;

  return (
    <div className="lineage-graph" ref={canvasRef}>
      <svg className="lineage-graph__edges" aria-hidden="true">
        {paths.map(path => (
          <path key={path.id} d={path.d} className={path.active ? 'is-active' : 'is-dim'} />
        ))}
      </svg>
      <div className="lineage-view">
        {lineageOrder.map((type, index) => {
          const column = nodes.filter(node => node.nodeType === type);
          return (
            <section key={type} className="lineage-stage">
              <header><span>{index + 1}</span>{title(type)}<small>{column.length}</small></header>
              <div>
                {column.length === 0 && <p className="lineage-stage__empty">None yet</p>}
                {column.map(node => {
                  const historical = node.qualityFlags.includes('historical');
                  const dimmed = relatedIds ? !relatedIds.has(node.nodeId) : historical;
                  return (
                    <button
                      key={node.nodeId}
                      ref={element => {
                        if (element) nodeRefs.current.set(node.nodeId, element);
                        else nodeRefs.current.delete(node.nodeId);
                      }}
                      className={[
                        selected?.nodeId === node.nodeId ? 'is-selected' : '',
                        dimmed ? 'is-dim' : '',
                        historical ? 'is-historical' : '',
                      ].filter(Boolean).join(' ')}
                      onClick={() => onSelect(node)}
                    >
                      <strong>{node.label}</strong>
                      <small>{historical ? 'Earlier decision' : node.qualityFlags.includes('current') ? 'Current' : node.qualityFlags[0] || 'Recorded'}</small>
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function AgencyCoordinationView({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (node: GraphNode) => void }) {
  const agencies = useMemo(() => {
    const names = new Map<string, { label: string; sent: number; received: number; node: GraphNode }>();
    for (const node of nodes.filter(item => item.nodeType === 'commitment')) {
      const label = String(node.state.ownerAgencyName ?? node.label);
      const current = names.get(label) ?? { label, sent: 0, received: 0, node };
      current.received += 1;
      names.set(label, current);
    }
    return [...names.values()];
  }, [nodes]);
  if (!nodes.length) return <GraphEmpty view="agency_coordination" />;
  return <div className="agency-view">
    <div className="agency-view__summary"><strong>{agencies.length}</strong> accountable organizations <strong>{edges.length}</strong> recorded handoffs</div>
    <div className="agency-board">{(agencies.length ? agencies : nodes.filter(node => node.nodeType === 'commitment')).map(item => {
      const node = 'node' in item ? item.node : item;
      const label = 'label' in item && typeof item.label === 'string' && !('nodeType' in item) ? item.label : node.label;
      return <button key={node.nodeId} className="agency-card" onClick={() => onSelect(node)}><span>{title(node.nodeType)}</span><strong>{label}</strong><dl><div><dt>State</dt><dd>{String(node.state.state ?? node.qualityFlags[0] ?? '—')}</dd></div><div><dt>Links</dt><dd>{edges.filter(edge => edge.fromNodeId === node.nodeId || edge.toNodeId === node.nodeId).length}</dd></div></dl><small>{String(node.state.requestedOutcome ?? node.label)}</small></button>;
    })}</div>
  </div>;
}

function Inspector({ node, nodes, edges, onClose }: { node: GraphNode | null; nodes: GraphNode[]; edges: GraphEdge[]; onClose: () => void }) {
  if (!node) return <aside className="graph-inspector graph-inspector--empty"><span>Inspector</span><p>Select a node to see what it is, when it was recorded, and what it connects to.</p></aside>;
  const relationships = edges.filter(edge => edge.fromNodeId === node.nodeId || edge.toNodeId === node.nodeId);
  const byId = new Map(nodes.map(item => [item.nodeId, item]));
  return (
    <aside className="graph-inspector">
      <button className="inspector-close" onClick={onClose}>Close</button>
      <span>Inspector</span>
      <h2>{node.label}</h2>
      <div className="inspector-meta">
        <b>{title(node.nodeType)}</b>
        <b>{node.qualityFlags.includes('historical') ? 'Historical' : 'Current'}</b>
        <b>v{node.version}</b>
      </div>
      <section>
        <h3>Record</h3>
        {Object.entries(node.state).slice(0, 8).map(([key, value]) => (
          <p key={key}><strong>{title(key)}</strong> {value === null || value === undefined || value === '' ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</p>
        ))}
      </section>
      <section>
        <h3>When</h3>
        <p>Valid from {new Date(node.validFrom).toLocaleString()}</p>
        <p>Updated {new Date(node.updatedAt).toLocaleString()}</p>
      </section>
      <section>
        <h3>Relationships</h3>
        {relationships.length
          ? relationships.map(edge => {
            const otherId = edge.fromNodeId === node.nodeId ? edge.toNodeId : edge.fromNodeId;
            const other = byId.get(otherId);
            const direction = edge.fromNodeId === node.nodeId ? 'to' : 'from';
            return <p key={edge.edgeId}>{title(edge.edgeType)} {direction} {other?.label ?? otherId}</p>;
          })
          : <p>No current relationships.</p>}
      </section>
    </aside>
  );
}

export function OperationalGraphWorkspace() {
  const [view, setView] = useState<GraphView>('decision_lineage');
  const [event, setEvent] = useState<OperationalEvent | null>(null);
  const [snapshot, setSnapshot] = useState<GraphSnapshot | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { void graphApi.activeEvent().then(setEvent).catch(error => setError(error.message)); }, []);
  useEffect(() => {
    if (!event) return;
    setLoading(true);
    setSelected(null);
    void graphApi.snapshot(event.eventId, view)
      .then(data => { setSnapshot(data); setError(null); })
      .catch(error => setError(error.message))
      .finally(() => setLoading(false));
  }, [event, view]);

  const history = useMemo(() => {
    const decisions = (snapshot?.nodes ?? [])
      .filter(node => node.nodeType === 'decision')
      .sort((left, right) => Date.parse(left.validFrom) - Date.parse(right.validFrom));
    return decisions;
  }, [snapshot]);

  return (
    <main className="graph-workspace">
      <header className="graph-header">
        <div>
          <span>Lineage</span>
          <h1>{viewLabels[view].title}</h1>
          <p>{viewLabels[view].subtitle}</p>
        </div>
        <div className="graph-header__status">
          <strong>{event?.name || 'Loading the current window'}</strong>
          <span>{snapshot ? `${snapshot.nodes.length} records · ${snapshot.edges.length} links · as of ${new Date(snapshot.asOf).toLocaleTimeString()}` : 'Waiting for records'}</span>
        </div>
      </header>
      <nav className="graph-tabs" aria-label="Operational graph views">
        {viewOrder.map(item => (
          <button key={item} className={item === view ? 'active' : ''} onClick={() => setView(item)}>
            <strong>{viewLabels[item].title}</strong>
            <span>{viewLabels[item].subtitle}</span>
          </button>
        ))}
      </nav>
      <div className="graph-body">
        <section className="graph-canvas">
          {loading ? <div className="graph-empty"><strong>Loading current graph state</strong></div>
            : error ? <div className="graph-empty graph-empty--error"><strong>Graph unavailable</strong><p>{error}</p></div>
              : snapshot && view === 'mobility' ? <MobilityFlow nodes={snapshot.nodes} edges={snapshot.edges} onSelect={setSelected} />
                : snapshot && view === 'decision_lineage' ? <DecisionLineage nodes={snapshot.nodes} edges={snapshot.edges} selected={selected} onSelect={setSelected} />
                  : snapshot ? <AgencyCoordinationView nodes={snapshot.nodes} edges={snapshot.edges} onSelect={setSelected} />
                    : null}
        </section>
        <Inspector node={selected} nodes={snapshot?.nodes ?? []} edges={snapshot?.edges ?? []} onClose={() => setSelected(null)} />
      </div>
      <footer className="graph-timeline">
        <span>History</span>
        {history.length === 0
          ? <div><strong>No recorded decisions yet</strong><small>{snapshot ? new Date(snapshot.generatedAt).toLocaleString() : 'Not loaded'}</small></div>
          : history.map(node => (
            <button key={node.nodeId} type="button" className={selected?.nodeId === node.nodeId ? 'is-active' : ''} onClick={() => setSelected(node)}>
              <strong>{node.label}</strong>
              <small>{node.qualityFlags.includes('historical') ? 'Earlier' : 'Current'} · {new Date(node.validFrom).toLocaleString()}</small>
            </button>
          ))}
      </footer>
    </main>
  );
}
