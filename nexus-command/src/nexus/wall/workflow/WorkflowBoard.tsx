import { createContext, useCallback, useContext, useMemo, useState, type DragEvent, type MouseEvent, type ReactNode } from 'react';
import {
  addEdge,
  Background,
  Controls,
  Handle,
  MarkerType,
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
import '@xyflow/react/dist/style.css';
import './workflow.css';
import {
  PALETTE,
  WORKFLOW_AGENTS,
  WORKFLOW_SOURCES,
  sourceIsLive,
  type WorkflowKind,
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

interface PalettePayload {
  kind: WorkflowKind;
  id: string;
  label: string;
  note: string;
}

type BoxData = {
  label: string;
  note: string;
  live?: boolean;
};

type SourceNode = Node<BoxData, 'source'>;
type AgentNode = Node<BoxData, 'agent'>;
type StakeholderNode = Node<BoxData, 'stakeholder'>;
type DecisionNode = Node<BoxData, 'decision'>;
type LaneNode = Node<{ label: string }, 'lane'>;
type WorkflowNode = SourceNode | AgentNode | StakeholderNode | DecisionNode | LaneNode;

const DnDContext = createContext<{
  payload: PalettePayload | null;
  setPayload: (next: PalettePayload | null) => void;
}>({ payload: null, setPayload: () => undefined });

const edgeDefaults: Partial<Edge> = {
  type: 'smoothstep',
  style: { stroke: '#e87722', strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: '#e87722' },
};

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

function sourceNode({ data }: NodeProps<SourceNode>) {
  return (
    <div className={`nx-wf-node nx-wf-node--source${data.live ? ' nx-wf-node--live' : ''}`}>
      <Handle type="source" position={Position.Right} />
      <span className="nx-wf-node__kicker">{data.live ? 'Live feed' : 'Data source'}</span>
      <span className="nx-wf-node__title">{data.label}</span>
      <span className="nx-wf-node__note">{data.note}</span>
    </div>
  );
}

function agentNode({ data }: NodeProps<AgentNode>) {
  return (
    <div className="nx-wf-node nx-wf-node--agent">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <span className="nx-wf-node__kicker">Agent</span>
      <span className="nx-wf-node__title">{data.label}</span>
      <span className="nx-wf-node__note">{data.note}</span>
    </div>
  );
}

function stakeholderNode({ data }: NodeProps<StakeholderNode>) {
  return (
    <div className="nx-wf-node nx-wf-node--stakeholder">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <span className="nx-wf-node__kicker">Stakeholder</span>
      <span className="nx-wf-node__title">{data.label}</span>
      <span className="nx-wf-node__note">{data.note}</span>
    </div>
  );
}

function decisionNode({ data }: NodeProps<DecisionNode>) {
  return (
    <div className="nx-wf-node nx-wf-node--decision">
      <Handle type="target" position={Position.Left} />
      <span className="nx-wf-node__kicker">Decision</span>
      <span className="nx-wf-node__title">{data.label}</span>
      <span className="nx-wf-node__note">{data.note}</span>
    </div>
  );
}

function laneNode({ data }: NodeProps<LaneNode>) {
  return <div className="nx-wf-lane">{data.label}</div>;
}

const nodeTypes = {
  source: sourceNode,
  agent: agentNode,
  stakeholder: stakeholderNode,
  decision: decisionNode,
  lane: laneNode,
} satisfies NodeTypes;

function buildGraph(feeds: WorkflowFeed[], stakeholder: string): { nodes: WorkflowNode[]; edges: Edge[] } {
  const live = haystack(feeds);
  const lanes: LaneNode[] = [
    { id: 'lane-sources', type: 'lane', position: { x: 24, y: -8 }, data: { label: 'Sources' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-agents', type: 'lane', position: { x: 320, y: -8 }, data: { label: 'Agents' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-sign', type: 'lane', position: { x: 620, y: -8 }, data: { label: 'Stakeholder' }, draggable: false, selectable: false, connectable: false },
    { id: 'lane-record', type: 'lane', position: { x: 900, y: -8 }, data: { label: 'Decision' }, draggable: false, selectable: false, connectable: false },
  ];
  const nodes: WorkflowNode[] = [
    ...lanes,
    ...WORKFLOW_SOURCES.map((item, index): SourceNode => ({
      id: item.id,
      type: 'source',
      position: { x: 24, y: 36 + index * 92 },
      data: { label: item.label, note: item.agency, live: sourceIsLive(item, live) },
    })),
    ...WORKFLOW_AGENTS.map((item, index): AgentNode => ({
      id: item.id,
      type: 'agent',
      position: { x: 320, y: 60 + index * 108 },
      data: { label: item.label, note: item.role },
    })),
    {
      id: 'stakeholder',
      type: 'stakeholder',
      position: { x: 620, y: 292 },
      data: { label: stakeholder || 'Named stakeholder', note: 'Signs the recommendation' },
    },
    {
      id: 'decision',
      type: 'decision',
      position: { x: 900, y: 292 },
      data: { label: 'Decision', note: 'Recorded, not actuated' },
    },
  ];

  const edges: Edge[] = [];
  for (const agent of WORKFLOW_AGENTS) {
    for (const connector of agent.connectors) {
      edges.push({
        id: `${connector}->${agent.id}`,
        source: connector,
        target: agent.id,
        animated: liveFor(connector, live),
      });
    }
    edges.push({
      id: `${agent.id}->stakeholder`,
      source: agent.id,
      target: 'stakeholder',
    });
  }
  edges.push({
    id: 'stakeholder->decision',
    source: 'stakeholder',
    target: 'decision',
    animated: true,
  });

  return { nodes, edges };
}

let dropSeq = 0;
const nextId = (kind: string, id: string) => `${kind}-${id}-${dropSeq++}`;

function nodeFromPalette(item: PalettePayload, position: XYPosition, live: string): WorkflowNode {
  const data: BoxData = {
    label: item.label,
    note: item.note,
    live: item.kind === 'source' ? liveFor(item.id, live) : undefined,
  };
  return {
    id: nextId(item.kind, item.id),
    type: item.kind,
    position,
    data,
  } as WorkflowNode;
}

const PALETTE_GROUPS: { title: string; kinds: WorkflowKind[] }[] = [
  { title: 'Sources', kinds: ['source'] },
  { title: 'Agents', kinds: ['agent'] },
  { title: 'Close', kinds: ['stakeholder', 'decision'] },
];

function Sidebar() {
  const { payload, setPayload } = useContext(DnDContext);
  return (
    <aside className="nx-workflow__rail">
      <div>
        <h2>Workflow</h2>
        <p>Drag a box onto the canvas, or tap one then tap the pane. Sources feed only the desks that may read them. The stakeholder signs. That record is the decision.</p>
      </div>
      <div className="nx-workflow__list">
        {PALETTE_GROUPS.map(group => (
          <div key={group.title} className="nx-workflow__group">
            <span className="nx-workflow__group-title">{group.title}</span>
            {PALETTE.filter(item => group.kinds.includes(item.kind)).map(item => {
              const armed = payload?.kind === item.kind && payload.id === item.id;
              return (
                <button
                  key={`${item.kind}-${item.id}`}
                  type="button"
                  aria-pressed={armed}
                  className={`nx-workflow__chip nx-workflow__chip--${item.kind}${armed ? ' nx-workflow__chip--armed' : ''}`}
                  draggable
                  onClick={() => setPayload(armed ? null : item)}
                  onDragStart={(event: DragEvent) => {
                    setPayload(item);
                    event.dataTransfer.setData('application/reactflow', JSON.stringify(item));
                    event.dataTransfer.effectAllowed = 'move';
                  }}
                >
                  <strong>{item.label}</strong>
                  <span>{item.note}</span>
                </button>
              );
            })}
          </div>
        ))}
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

  const place = useCallback((item: PalettePayload, clientX: number, clientY: number) => {
    const position = screenToFlowPosition({ x: clientX, y: clientY });
    setNodes(current => current.concat(nodeFromPalette(item, position, live)));
    setPayload(null);
  }, [live, screenToFlowPosition, setNodes, setPayload]);

  const onConnect = useCallback((params: Connection) => {
    setEdges(current => addEdge({ ...params }, current));
  }, [setEdges]);

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: DragEvent) => {
    event.preventDefault();
    const item = readPayload(event.dataTransfer.getData('application/reactflow'), payload);
    if (!item) return;
    place(item, event.clientX, event.clientY);
  }, [payload, place]);

  const onPaneClick = useCallback((event: MouseEvent) => {
    if (!payload) return;
    place(payload, event.clientX, event.clientY);
  }, [payload, place]);

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
        nodeTypes={nodeTypes}
        defaultEdgeOptions={edgeDefaults}
        fitView
        minZoom={0.25}
        maxZoom={1.6}
        colorMode="dark"
        deleteKeyCode={['Backspace', 'Delete']}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
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
