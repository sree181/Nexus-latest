import type { DataClassification, OperationalMode, PrincipalContext } from '../operational/domain.js';

export type GraphView = 'mobility' | 'decision_lineage' | 'agency_coordination';
export type GraphEntityKind = 'node' | 'edge';

export interface GraphNodeInput {
  nodeType: string;
  externalKey: string;
  label: string;
  ownerAgencyId?: string;
  authorityUri?: string;
  dataClassification: DataClassification;
  geometryGeojson?: Record<string, unknown> | null;
  state: Record<string, unknown>;
  qualityFlags?: string[];
  validFrom: string;
  validUntil?: string | null;
  active?: boolean;
  evidenceIds?: string[];
}

export interface GraphEdgeEndpointInput {
  nodeType: string;
  externalKey: string;
}

export interface GraphEdgeInput {
  edgeType: string;
  externalKey: string;
  from: GraphEdgeEndpointInput;
  to: GraphEdgeEndpointInput;
  directed?: boolean;
  ownerAgencyId?: string;
  authorityUri?: string;
  dataClassification: DataClassification;
  geometryGeojson?: Record<string, unknown> | null;
  state: Record<string, unknown>;
  qualityFlags?: string[];
  validFrom: string;
  validUntil?: string | null;
  active?: boolean;
  evidenceIds?: string[];
}

export interface GraphIngestionBatch {
  mode: OperationalMode;
  schemaVersion: string;
  nodes: GraphNodeInput[];
  edges: GraphEdgeInput[];
}

export interface GraphIngestionResult {
  batchId: string;
  status: 'succeeded' | 'partial';
  nodeCount: number;
  edgeCount: number;
  unchangedCount: number;
  rejectedCount: number;
  requestId: string;
}

export interface GraphNode {
  nodeId: string;
  eventId: string;
  mode: OperationalMode;
  nodeType: string;
  externalKey: string;
  label: string;
  ownerAgencyId: string | null;
  sourceId: string | null;
  authorityUri: string | null;
  dataClassification: DataClassification;
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
  mode: OperationalMode;
  edgeType: string;
  externalKey: string;
  fromNodeId: string;
  toNodeId: string;
  directed: boolean;
  ownerAgencyId: string | null;
  sourceId: string | null;
  authorityUri: string | null;
  dataClassification: DataClassification;
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
  mode: OperationalMode;
  view: GraphView;
  asOf: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  generatedAt: string;
}

export interface GraphStateChange {
  stateChangeId: string;
  entityKind: GraphEntityKind;
  entityId: string;
  entityType: string;
  changeType: string;
  previousVersion: number | null;
  newVersion: number;
  previousState: Record<string, unknown> | null;
  newState: Record<string, unknown>;
  qualityFlags: string[];
  sourceId: string | null;
  evidenceIds: string[];
  requestId: string | null;
  occurredAt: string;
}

export interface GraphQueryContext {
  principal: PrincipalContext;
  mode: OperationalMode;
}
