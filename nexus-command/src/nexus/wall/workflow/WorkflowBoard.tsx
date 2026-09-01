import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type CSSProperties,
  type DragEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';
import {
  addEdge,
  Background,
  ConnectionLineType,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  type XYPosition,
} from '@xyflow/react';
import { Bot, Database, FileCheck2, Link2, MousePointer2, RotateCcw, Trash2, UsersRound } from 'lucide-react';
import { SiBox, SiGoogledrive } from 'react-icons/si';
/**
 * CIVIC INSTRUMENT PANEL
 * Stage 6 is an editable accountability route: connector → source/agent → stakeholder → signed record.
 */
import '@xyflow/react/dist/style.css';
import './workflow.css';
import {
  PALETTE,
  WORKFLOW_AGENTS,
  WORKFLOW_CONNECTORS,
  WORKFLOW_SOURCES,
  sourceIsLive,
  type WorkflowKind,
  type WorkflowPaletteItem,
} from './catalog';

export interface WorkflowFeed {
  key?: string;
  name?: string;
  connectorCode?: string | null;
  sourceCode?: string;
}

interface BoardProps {
  feeds?: WorkflowFeed[];
  stakeholder?: string;
}

type PalettePayload = WorkflowPaletteItem;

type BoxData = {
  label: string;
  note: string;
  icon: string;
  brand?: string;
  live?: boolean;
};

type ConnectorNode = Node<BoxData, 'connector'>;
type SourceNode = Node<BoxData, 'source'>;
type AgentNode = Node<BoxData, 'agent'>;
type StakeholderNode = Node<BoxData, 'stakeholder'>;
type DecisionNode = Node<BoxData, 'decision'>;
type LaneNode = Node<{ label: string }, 'lane'>;
type WorkflowNode = ConnectorNode | SourceNode | AgentNode | StakeholderNode | DecisionNode | LaneNode;

const DnDContext = createContext<{
  payload: PalettePayload | null;
  setPayload: (next: PalettePayload | null) => void;
}>({ payload: null, setPayload: () => undefined });

const edgeDefaults: Partial<Edge> = {
  type: 'smoothstep',
  style: { stroke: '#f2a33a', strokeWidth: 2.4 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: '#f2a33a' },
};

function ComponentMark({ icon, brand }: { icon: string; brand?: string }) {
  const style = { color: brand || 'currentColor' };
  if (icon === 'box') return <SiBox aria-hidden="true" style={style} />;
  if (icon === 'google-drive') return <SiGoogledrive aria-hidden="true" style={style} />;
  if (icon === 'sharepoint') return <img src="https://upload.wikimedia.org/wikipedia/commons/e/ec/Microsoft_Office_SharePoint_%282019%E2%80%932025%29.svg" alt="" />;
  if (icon === 'agent') return <Bot aria-hidden="true" />;
  if (icon === 'stakeholder') return <UsersRound aria-hidden="true" />;
  if (icon === 'decision') return <FileCheck2 aria-hidden="true" />;
  return <Database aria-hidden="true" />;
}

function haystack(feeds: WorkflowFeed[]): string {
  return feeds.map(item => [item.connectorCode, item.sourceCode, item.name, item.key].filter(Boolean).join(' ')).join(' | ');
}

function liveFor(id: string, live: string): boolean {
  const source = WORKFLOW_SOURCES.find(item => item.id === id);
  return source ? sourceIsLive(source, live) : false;
}

function readPayload(raw: string, fallback: PalettePayload | null): PalettePayload | null {
  if (raw) {
    try {
      return JSON.parse(raw) as PalettePayload;
    } catch {
      return fallback;
    }
  }
  return fallback;
}

function NodeShell({ data, kind, target, source }: { data: BoxData; kind: WorkflowKind; target?: boolean; source?: boolean }) {
  return (
    <div className={`nx-wf-node nx-wf-node--${kind}${data.live ? ' nx-wf-node--live' : ''}`} data-workflow-node={data.label} style={{ '--node-brand': data.brand || undefined } as CSSProperties}>
      {target ? <Handle type="target" position={Position.Left} aria-label={`Input to ${data.label}`} /> : null}
      {source ? <Handle type="source" position={Position.Right} aria-label={`Output from ${data.label}`} /> : null}
      <span className="nx-wf-node__mark"><ComponentMark icon={data.icon} brand={data.brand} /></span>
      <span className="nx-wf-node__copy">
        <span className="nx-wf-node__kicker">{kind === 'connector' ? 'Connector' : data.live ? 'Live source' : kind}</span>
        <strong className="nx-wf-node__title">{data.label}</strong>
        <span className="nx-wf-node__note">{data.note}</span>
      </span>
    </div>
  );
}

function connectorNode({ data }: NodeProps<ConnectorNode>) { return <NodeShell data={data} kind="connector" source />; }
function sourceNode({ data }: NodeProps<SourceNode>) { return <NodeShell data={data} kind="source" source />; }
function agentNode({ data }: NodeProps<AgentNode>) { return <NodeShell data={data} kind="agent" target source />; }
function stakeholderNode({ data }: NodeProps<StakeholderNode>) { return <NodeShell data={data} kind="stakeholder" target source />; }
function decisionNode({ data }: NodeProps<DecisionNode>) { return <NodeShell data={data} kind="decision" target />; }
function laneNode({ data }: NodeProps<LaneNode>) { return <div className="nx-wf-lane">{data.label}</div>; }

const nodeTypes = {
  connector: connectorNode,
  source: sourceNode,
  agent: agentNode,
  stakeholder: stakeholderNode,
  decision: decisionNode,
  lane: laneNode,
} satisfies NodeTypes;

function buildGraph(feeds: WorkflowFeed[], stakeholder: string): { nodes: WorkflowNode[]; edges: Edge[] } {
  const live = haystack(feeds);
  const lanes: LaneNode[] = [
    { id: 'lane-connectors', type: 'lane', position: { x: 24, y: -18 }, data: { label: '01 / Connectors' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-sources', type: 'lane', position: { x: 300, y: -18 }, data: { label: '02 / Operational sources' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-agents', type: 'lane', position: { x: 610, y: -18 }, data: { label: '03 / Agents' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-sign', type: 'lane', position: { x: 930, y: -18 }, data: { label: '04 / Stakeholder' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-record', type: 'lane', position: { x: 1240, y: -18 }, data: { label: '05 / Decision' }, draggable: false, selectable: false, connectable: false },
  ];
  const nodes: WorkflowNode[] = [
    ...lanes,
    ...WORKFLOW_CONNECTORS.map((item, index): ConnectorNode => ({
      id: item.id,
      type: 'connector',
      position: { x: 24, y: 42 + index * 104 },
      data: { label: item.label, note: item.agency, icon: item.icon, brand: item.brand },
    })),
    ...WORKFLOW_SOURCES.map((item, index): SourceNode => ({
      id: item.id,
      type: 'source',
      position: { x: 300, y: 42 + index * 82 },
      data: { label: item.label, note: item.agency, icon: 'source', live: sourceIsLive(item, live) },
    })),
    ...WORKFLOW_AGENTS.map((item, index): AgentNode => ({
      id: item.id,
      type: 'agent',
      position: { x: 610, y: 72 + index * 116 },
      data: { label: item.label, note: item.role, icon: 'agent' },
    })),
    {
      id: 'stakeholder',
      type: 'stakeholder',
      position: { x: 930, y: 320 },
      data: { label: stakeholder || 'Named stakeholder', note: 'Reviews and signs', icon: 'stakeholder' },
    },
    {
      id: 'decision',
      type: 'decision',
      position: { x: 1240, y: 320 },
      data: { label: 'Decision record', note: 'Signed, recorded, not actuated', icon: 'decision' },
    },
  ];

  const edges: Edge[] = [];
  for (const agent of WORKFLOW_AGENTS) {
    for (const connector of agent.connectors) {
      edges.push({ id: `${connector}->${agent.id}`, source: connector, target: agent.id, animated: liveFor(connector, live) });
    }
    edges.push({ id: `${agent.id}->stakeholder`, source: agent.id, target: 'stakeholder' });
  }
  edges.push({ id: 'stakeholder->decision', source: 'stakeholder', target: 'decision', animated: true });
  return { nodes, edges };
}

let dropSeq = 0;
const nextId = (kind: string, id: string) => `${kind}-${id}-${dropSeq++}`;

function nodeFromPalette(item: PalettePayload, position: XYPosition, live: string): WorkflowNode {
  return {
    id: nextId(item.kind, item.id),
    type: item.kind,
    position,
    data: {
      label: item.label,
      note: item.note,
      icon: item.icon,
      brand: item.brand,
      live: item.kind === 'source' ? liveFor(item.id, live) : undefined,
    },
  } as WorkflowNode;
}

const PALETTE_GROUPS: { title: string; kinds: WorkflowKind[] }[] = [
  { title: 'Cloud connectors', kinds: ['connector'] },
  { title: 'Operational sources', kinds: ['source'] },
  { title: 'Agents', kinds: ['agent'] },
  { title: 'Accountability', kinds: ['stakeholder', 'decision'] },
];

function Sidebar() {
  const { payload, setPayload } = useContext(DnDContext);
  return (
    <aside className="nx-workflow__rail">
      <header className="nx-workflow__intro">
        <span className="nx-workflow__index">06 / Workflow builder</span>
        <h2>Build the accountable route.</h2>
        <p>Drag any component onto the canvas. Draw a connection from its right port to the next component’s left port.</p>
      </header>
      <div className="nx-workflow__list" aria-label="Workflow component catalog">
        {PALETTE_GROUPS.map(group => {
          const items = PALETTE.filter(item => group.kinds.includes(item.kind));
          return (
            <section key={group.title} className="nx-workflow__group">
              <header><span>{group.title}</span><small>{items.length}</small></header>
              {items.map(item => {
                const armed = payload?.kind === item.kind && payload.id === item.id;
                return (
                  <button
                    key={`${item.kind}-${item.id}`}
                    type="button"
                    aria-pressed={armed}
                    className={`nx-workflow__chip nx-workflow__chip--${item.kind}${armed ? ' nx-workflow__chip--armed' : ''}`}
                    data-workflow-palette={item.label}
                    draggable
                    onClick={() => setPayload(armed ? null : item)}
                    onDragStart={(event: DragEvent) => {
                      setPayload(item);
                      event.dataTransfer.setData('application/reactflow', JSON.stringify(item));
                      event.dataTransfer.effectAllowed = 'move';
                    }}
                  >
                    <span className="nx-workflow__chip-mark"><ComponentMark icon={item.icon} brand={item.brand} /></span>
                    <span><strong>{item.label}</strong><small>{item.note}</small></span>
                  </button>
                );
              })}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function Canvas({ feeds, stakeholder }: BoardProps) {
  const { screenToFlowPosition } = useReactFlow();
  const { payload, setPayload } = useContext(DnDContext);
  const live = useMemo(() => haystack(feeds || []), [feeds]);
  const seed = useMemo(() => buildGraph(feeds || [], stakeholder || 'Named stakeholder'), [feeds, stakeholder]);
  const [nodes, setNodes, onNodesChange] = useNodesState(seed.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(seed.edges);
  const [selection, setSelection] = useState<{ nodes: string[]; edges: string[] }>({ nodes: [], edges: [] });

  const place = useCallback((item: PalettePayload, clientX: number, clientY: number) => {
    const position = screenToFlowPosition({ x: clientX, y: clientY });
    setNodes(current => current.concat(nodeFromPalette(item, position, live)));
    setPayload(null);
  }, [live, screenToFlowPosition, setNodes, setPayload]);

  const onConnect = useCallback((params: Connection) => {
    if (!params.source || !params.target || params.source === params.target) return;
    setEdges(current => addEdge({
      ...params,
      id: `${params.source}->${params.target}-${Date.now()}`,
      animated: true,
      ...edgeDefaults,
    }, current));
  }, [setEdges]);

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: DragEvent) => {
    event.preventDefault();
    const item = readPayload(event.dataTransfer.getData('application/reactflow'), payload);
    if (item) place(item, event.clientX, event.clientY);
  }, [payload, place]);

  const onPaneClick = useCallback((event: MouseEvent) => {
    if (payload) place(payload, event.clientX, event.clientY);
  }, [payload, place]);

  const removeSelection = useCallback(() => {
    const nodeIds = new Set(selection.nodes);
    const edgeIds = new Set(selection.edges);
    setNodes(current => current.filter(node => !nodeIds.has(node.id)));
    setEdges(current => current.filter(edge => !edgeIds.has(edge.id) && !nodeIds.has(edge.source) && !nodeIds.has(edge.target)));
    setSelection({ nodes: [], edges: [] });
  }, [selection, setEdges, setNodes]);

  const onSelectionChange = useCallback(({ nodes: selectedNodes, edges: selectedEdges }: { nodes: WorkflowNode[]; edges: Edge[] }) => {
    const next = { nodes: selectedNodes.map(node => node.id), edges: selectedEdges.map(edge => edge.id) };
    setSelection(current => (
      current.nodes.join('|') === next.nodes.join('|') && current.edges.join('|') === next.edges.join('|')
        ? current
        : next
    ));
  }, []);

  const reset = useCallback(() => {
    setNodes(seed.nodes);
    setEdges(seed.edges);
    setSelection({ nodes: [], edges: [] });
    setPayload(null);
  }, [seed, setEdges, setNodes, setPayload]);

  return (
    <div className="nx-workflow__pane">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onPaneClick={onPaneClick}
        onSelectionChange={onSelectionChange}
        nodeTypes={nodeTypes}
        defaultEdgeOptions={edgeDefaults}
        connectionLineType={ConnectionLineType.SmoothStep}
        connectionLineStyle={{ stroke: '#f2a33a', strokeWidth: 2.8 }}
        fitView
        fitViewOptions={{ padding: 0.06, minZoom: 0.4, maxZoom: 1.5 }}
        minZoom={0.18}
        maxZoom={2}
        colorMode="dark"
        panOnDrag
        zoomOnPinch
        preventScrolling
        nodeDragThreshold={8}
        deleteKeyCode={['Backspace', 'Delete']}
        proOptions={{ hideAttribution: true }}
      >
        <Panel position="top-right" className="nx-workflow__toolbar">
          <span><MousePointer2 aria-hidden="true" />Drag components</span>
          <span><Link2 aria-hidden="true" />Draw port to port</span>
          <button type="button" onClick={removeSelection} disabled={!selection.nodes.length && !selection.edges.length}><Trash2 aria-hidden="true" />Remove</button>
          <button type="button" onClick={reset}><RotateCcw aria-hidden="true" />Reset</button>
        </Panel>
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeStrokeWidth={3} />
        <Background gap={22} size={1} />
      </ReactFlow>
    </div>
  );
}

function DnDProvider({ children }: { children: ReactNode }) {
  const [payload, setPayload] = useState<PalettePayload | null>(null);
  const value = useMemo(() => ({ payload, setPayload }), [payload]);
  return <DnDContext.Provider value={value}>{children}</DnDContext.Provider>;
}

export default function WorkflowBoard({ feeds = [], stakeholder = 'Named stakeholder' }: BoardProps) {
  return (
    <div data-screen-label="Workflow" className="nx-workflow">
      <ReactFlowProvider>
        <DnDProvider>
          <Sidebar />
          <Canvas feeds={feeds} stakeholder={stakeholder} />
        </DnDProvider>
      </ReactFlowProvider>
    </div>
  );
}
