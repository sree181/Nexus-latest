export type GraphView = 'mobility' | 'decision_lineage' | 'agency_coordination';

export interface GraphNode {
  nodeId: string;
  eventId: string;
  mode: 'live' | 'training' | 'replay';
  nodeType: string;
  externalKey: string;
  label: string;
  ownerAgencyId: string | null;
  sourceId: string | null;
  authorityUri: string | null;
  dataClassification: 'live' | 'near_real_time' | 'reference' | 'operational' | 'restricted';
  geometryGeojson: Record<string, unknown> | null;
  state: Record<string, unknown>;
  qualityFlags: string[];
  validFrom: string;
  validUntil: string | null;
  active: boolean;
  version: number;
  updatedAt: string;
}

export interface GraphEdge {
  edgeId: string;
  eventId: string;
  mode: 'live' | 'training' | 'replay';
  edgeType: string;
  externalKey: string;
  fromNodeId: string;
  toNodeId: string;
  directed: boolean;
  ownerAgencyId: string | null;
  sourceId: string | null;
  authorityUri: string | null;
  dataClassification: GraphNode['dataClassification'];
  geometryGeojson: Record<string, unknown> | null;
  state: Record<string, unknown>;
  qualityFlags: string[];
  validFrom: string;
  validUntil: string | null;
  active: boolean;
  version: number;
  updatedAt: string;
}

export interface GraphSnapshot {
  eventId: string;
  mode: 'live' | 'training' | 'replay';
  view: GraphView;
  asOf: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  generatedAt: string;
}

export interface AgencyCoordination {
  eventId: string;
  mode: string;
  commitments: Array<Record<string, unknown>>;
  generatedAt: string;
}
