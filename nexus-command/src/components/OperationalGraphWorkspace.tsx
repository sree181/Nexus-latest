import { useEffect, useMemo, useState } from 'react';
import { graphApi } from '../graphApi';
import type { GraphEdge, GraphNode, GraphSnapshot, GraphView } from '../graphTypes';
import type { OperationalEvent } from '../operationalTypes';
import '../graphWorkspace.css';

const viewLabels: Record<GraphView, { title: string; subtitle: string }> = {
  mobility: { title: 'Mobility Flow', subtitle: 'Roads, lots, transit, closures, capacity, and emergency corridors' },
  decision_lineage: { title: 'Decision Lineage', subtitle: 'Evidence to finding, recommendation, authority, commitment, and verification' },
  agency_coordination: { title: 'Agency Coordination', subtitle: 'Accountable ownership, requests, approvals, blockers, and verification' },
};

function title(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function GraphEmpty({ view }: { view: GraphView }) {
  return <div className="graph-empty"><strong>No {viewLabels[view].title.toLowerCase()} records yet</strong><p>Authoritative graph batches will appear here after an approved source publishes nodes and relationships for the active event.</p></div>;
}

function MobilityFlow({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (node: GraphNode) => void }) {
  const groups = useMemo(() => Object.entries(nodes.reduce<Record<string, GraphNode[]>>((result, node) => {
    (result[node.nodeType] ??= []).push(node);
    return result;
  }, {})), [nodes]);
  if (!nodes.length) return <GraphEmpty view="mobility" />;
  return <div className="mobility-flow">
    <div className="mobility-flow__metrics">
      <span><strong>{nodes.length}</strong> current assets</span><span><strong>{edges.length}</strong> active relationships</span>
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

const lineageOrder = ['evidence','finding','incident','recommendation','decision','commitment','verification'];
function DecisionLineage({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (node: GraphNode) => void }) {
  if (!nodes.length) return <GraphEmpty view="decision_lineage" />;
  return <div className="lineage-view">{lineageOrder.map((type, index) => <section key={type} className="lineage-stage">
    <header><span>{index + 1}</span>{title(type)}</header>
    <div>{nodes.filter(node => node.nodeType === type).map(node => <button key={node.nodeId} onClick={() => onSelect(node)}><strong>{node.label}</strong><small>{node.qualityFlags.length ? node.qualityFlags.join(', ') : 'Current'}</small></button>)}</div>
    {index < lineageOrder.length - 1 && <i aria-hidden="true">→</i>}
  </section>)}</div>;
}

function AgencyCoordinationView({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (node: GraphNode) => void }) {
  const agencies = nodes.filter(node => ['agency', 'operational_team'].includes(node.nodeType));
  if (!agencies.length) return <GraphEmpty view="agency_coordination" />;
  return <div className="agency-view">
    <div className="agency-view__summary"><strong>{agencies.length}</strong> accountable organizations <strong>{edges.length}</strong> active handoffs</div>
    <div className="agency-board">{agencies.map(agency => {
      const outbound = edges.filter(edge => edge.fromNodeId === agency.nodeId);
      const inbound = edges.filter(edge => edge.toNodeId === agency.nodeId);
      return <button key={agency.nodeId} className="agency-card" onClick={() => onSelect(agency)}><span>{title(agency.nodeType)}</span><strong>{agency.label}</strong><dl><div><dt>Requests sent</dt><dd>{outbound.length}</dd></div><div><dt>Requests received</dt><dd>{inbound.length}</dd></div></dl><small>{agency.qualityFlags.length ? agency.qualityFlags.join(', ') : 'No active data-quality warning'}</small></button>;
    })}</div>
  </div>;
}

function Inspector({ node, edges, onClose }: { node: GraphNode | null; edges: GraphEdge[]; onClose: () => void }) {
  if (!node) return <aside className="graph-inspector graph-inspector--empty"><span>ENTITY INSPECTOR</span><p>Select a node to review current state, provenance, ownership, and relationships.</p></aside>;
  const relationships = edges.filter(edge => edge.fromNodeId === node.nodeId || edge.toNodeId === node.nodeId);
  return <aside className="graph-inspector"><button className="inspector-close" onClick={onClose}>Close</button><span>ENTITY INSPECTOR</span><h2>{node.label}</h2><div className="inspector-meta"><b>{title(node.nodeType)}</b><b>{title(node.dataClassification)}</b><b>Version {node.version}</b></div><section><h3>Current state</h3><pre>{JSON.stringify(node.state, null, 2)}</pre></section><section><h3>Provenance</h3><p>{node.authorityUri || 'Authority URI not supplied'}</p><p>Valid from {new Date(node.validFrom).toLocaleString()}</p></section><section><h3>Relationships</h3>{relationships.length ? relationships.map(edge => <p key={edge.edgeId}>{title(edge.edgeType)} · v{edge.version}</p>) : <p>No current relationships.</p>}</section></aside>;
}

export function OperationalGraphWorkspace() {
  const [view, setView] = useState<GraphView>('mobility');
  const [event, setEvent] = useState<OperationalEvent | null>(null);
  const [snapshot, setSnapshot] = useState<GraphSnapshot | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { void graphApi.activeEvent().then(setEvent).catch(error => setError(error.message)); }, []);
  useEffect(() => {
    if (!event) return;
    setLoading(true); setSelected(null);
    void graphApi.snapshot(event.eventId, view).then(data => { setSnapshot(data); setError(null); }).catch(error => setError(error.message)).finally(() => setLoading(false));
  }, [event, view]);

  return <main className="graph-workspace">
    <header className="graph-header"><div><span>Lineage</span><h1>{viewLabels[view].title}</h1><p>{viewLabels[view].subtitle}</p></div><div className="graph-header__status"><strong>{event?.name || 'Loading the current window'}</strong><span>{snapshot ? `As of ${new Date(snapshot.asOf).toLocaleTimeString()}` : 'Waiting for records'}</span></div></header>
    <nav className="graph-tabs" aria-label="Operational graph views">{(Object.keys(viewLabels) as GraphView[]).map(item => <button key={item} className={item === view ? 'active' : ''} onClick={() => setView(item)}><strong>{viewLabels[item].title}</strong><span>{viewLabels[item].subtitle}</span></button>)}</nav>
    <div className="graph-body"><section className="graph-canvas">{loading ? <div className="graph-empty"><strong>Loading current graph state</strong></div> : error ? <div className="graph-empty graph-empty--error"><strong>Graph unavailable</strong><p>{error}</p></div> : snapshot && view === 'mobility' ? <MobilityFlow nodes={snapshot.nodes} edges={snapshot.edges} onSelect={setSelected} /> : snapshot && view === 'decision_lineage' ? <DecisionLineage nodes={snapshot.nodes} edges={snapshot.edges} onSelect={setSelected} /> : snapshot ? <AgencyCoordinationView nodes={snapshot.nodes} edges={snapshot.edges} onSelect={setSelected} /> : null}</section><Inspector node={selected} edges={snapshot?.edges ?? []} onClose={() => setSelected(null)} /></div>
    <footer className="graph-timeline"><span>History</span><div><i /><strong>Current records</strong><small>{snapshot ? new Date(snapshot.generatedAt).toLocaleString() : 'Not loaded'}</small></div><button disabled={!selected}>Open selected history</button></footer>
  </main>;
}
