import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl';
import { OperationalApiError, operationalApi } from '../operationalApi';
import type {
  Commitment,
  Incident,
  OperationalObservation,
  OperationalSnapshot,
  AgentFinding,
  PrincipalContext,
  Recommendation,
  ReferenceLayer,
  ReferenceLayerDefinition,
  ScenarioPack,
  SourceHealth,
  SystemStatus,
} from '../operationalTypes';
import { DeskConfigDialog } from './DeskConfigDialog';
import { NoOperatingWindowScreen, OperatingWindowChip, OperatingWindowDialog } from './OperatingWindow';
import {
  classificationLabel,
  commitmentLabel,
  connectionLabel,
  DESK_ICONS,
  DESK_ORDER,
  deskCallsign,
  deskName,
  deskStatusLabel,
  phaseLabel,
  QUEUE_BADGE_ICONS,
  queueAlert,
  queueBadges,
  serviceLabel,
  severityLabel,
} from '../uiCopy';

type DecisionAction = 'approve' | 'reject' | 'request_revision' | 'escalate';

interface DecisionDialogState {
  action: DecisionAction;
  recommendation: Recommendation;
}

const decisionLabels: Record<DecisionAction, string> = {
  approve: 'Approve',
  reject: 'Decline',
  request_revision: 'Send back',
  escalate: 'Escalate',
};

function formatTime(value: string | null): string {
  if (!value) return 'Not available';
  return new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function formatRelativeSeconds(seconds: number | null): string {
  if (seconds === null) return 'No observations';
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s ago`;
}

function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

function StatusDot({ status }: { status: SourceHealth['status'] }) {
  return <span className={`status-dot status-dot--${status}`} aria-hidden="true" />;
}

function Header({
  status, snapshot, principal, pack, canManage, onChangeWindow, onCloseWindow,
}: {
  status: SystemStatus;
  snapshot: OperationalSnapshot;
  principal: PrincipalContext;
  pack: ScenarioPack | null;
  canManage: boolean;
  onChangeWindow: () => void;
  onCloseWindow: () => void;
}) {
  const isReview = status.database === 'review_repository';
  const event = snapshot.event;
  const connectedSources = snapshot.sources.filter(source => source.connectionStatus === 'connected').length;
  const sourcesRequiringAction = snapshot.sources.length - connectedSources;
  return (
    <header className="ops-header">
      <span className={`live-pill ${isReview ? 'live-pill--review' : 'live-pill--live'}`}>
        <i aria-hidden="true" />
        {isReview ? 'Practice data' : 'Live'}
      </span>
      <div className="now-chip">
        <span>Now coordinating</span>
        <strong>{event.name}</strong>
      </div>
      <dl className="header-meta">
        <div>
          <dt>Phase</dt>
          <dd>{phaseLabel(event.phase)}</dd>
        </div>
        <div>
          <dt>Owner</dt>
          <dd>{event.commandOwner?.displayName || 'Unassigned'}</dd>
        </div>
        <div>
          <dt>Feeds</dt>
          <dd className={sourcesRequiringAction ? 'text-warning' : 'text-positive'}>
            {connectedSources} connected{sourcesRequiringAction ? ` · ${sourcesRequiringAction} need attention` : ''}
          </dd>
        </div>
      </dl>
      <OperatingWindowChip
        event={event}
        pack={pack}
        canManage={canManage}
        onChange={onChangeWindow}
        onClose={onCloseWindow}
      />
      <div className="operator-menu" aria-label="Signed in operator">
        <span>{principal.displayName}</span>
        <strong>{principal.agencyName}</strong>
      </div>
    </header>
  );
}

function ReviewQueue({
  incidents, selectedId, recommendation, onSelect,
}: {
  incidents: Incident[];
  selectedId: string | null;
  recommendation: Recommendation | null;
  onSelect: (incidentId: string) => void;
}) {
  return (
    <aside className="queue-pane" aria-label="Decisions waiting">
      <header className="pane-head">
        <div>
          <span>Needs your review</span>
          <strong>{recommendation ? '1 waiting' : 'Clear'}</strong>
        </div>
        <p>{recommendation ? 'Open a card, read the recommendation, then approve or send it back.' : 'No named rule has opened an incident in this window.'}</p>
      </header>
      <div className="queue-list">
        {incidents.length === 0 && (
          <div className="empty-state">
            <span className="empty-state__code">Clear</span>
            <h2>Nothing needs attention</h2>
            <p>When a named rule fires on official feed data, it will appear here.</p>
          </div>
        )}
        {incidents.map(incident => {
          const selected = incident.incidentId === selectedId;
          const due = recommendation?.incidentId === incident.incidentId ? recommendation.expiresAt : null;
          return (
            <button
              key={incident.incidentId}
              type="button"
              className={selected ? 'queue-card queue-card--active' : 'queue-card'}
              onClick={() => onSelect(incident.incidentId)}
              aria-pressed={selected}
            >
              <div className="queue-card__meta">
                <span className={`severity-badge severity-badge--${incident.severity}`}>{severityLabel(incident.severity)}</span>
                <span>{due ? `Decide by ${formatTime(due)}` : formatTime(incident.detectedAt)}</span>
              </div>
              <strong>{incident.title}</strong>
              <p className="queue-alert">
                <svg className="queue-alert__icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path fillRule="evenodd" d="M12 3.2 2.4 20.5h19.2L12 3.2Zm.9 13.6h-1.8v-1.7h1.8v1.7Zm0-3.2h-1.8V9.4h1.8v4.2Z" />
                </svg>
                <span>{queueAlert(incident.whyItMatters)}</span>
              </p>
              <div className="queue-badges">
                {queueBadges(incident.affectedServices).map(badge => (
                  <span key={badge} className={`queue-badge queue-badge--${badge.toLowerCase().replace(/\s+/g, '-')}`}>
                    <img src={QUEUE_BADGE_ICONS[badge]} alt="" />
                    {badge}
                  </span>
                ))}
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function OperationalMap({ incident, sources, observations }: { incident: Incident | null; sources: SourceHealth[]; observations: OperationalObservation[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerRef = useRef<MapLibreMarker | null>(null);
  const observationMarkersRef = useRef<MapLibreMarker[]>([]);
  const referenceCacheRef = useRef(new Map<string, ReferenceLayer>());
  const [mapReady, setMapReady] = useState(false);
  const [referenceCatalog, setReferenceCatalog] = useState<ReferenceLayerDefinition[]>([]);
  const [activeReference, setActiveReference] = useState<string[]>([]);
  const [referenceError, setReferenceError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let createdMap: MapLibreMap | null = null;
    let observer: ResizeObserver | null = null;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (cancelled || !containerRef.current) return;
      createdMap = new maplibregl.Map({
        container: containerRef.current,
        style: {
          version: 8,
          sources: {
            satellite: {
              type: 'raster',
              tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
              tileSize: 256,
              maxzoom: 19,
              attribution: '&copy; Esri',
            },
          },
          layers: [{
            id: 'satellite',
            type: 'raster',
            source: 'satellite',
            paint: { 'raster-brightness-max': 0.58, 'raster-brightness-min': 0.02, 'raster-contrast': 0.12, 'raster-saturation': -0.35 },
          }],
        },
        center: [-85.4808, 32.6067],
        zoom: 14,
        minZoom: 12.5,
        maxZoom: 18,
        maxBounds: [[-85.57, 32.55], [-85.39, 32.67]],
        attributionControl: false,
        renderWorldCopies: false,
      });
      createdMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
      mapRef.current = createdMap;
      createdMap.once('load', () => { if (!cancelled) setMapReady(true); });
      observer = new ResizeObserver(() => createdMap?.resize());
      observer.observe(containerRef.current);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      markerRef.current?.remove();
      observationMarkersRef.current.forEach(marker => marker.remove());
      createdMap?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const coordinates = incident?.locationGeojson?.coordinates;
    if (!mapReady || !mapRef.current || !coordinates || coordinates.length < 2) return;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (!mapRef.current) return;
      markerRef.current?.remove();
      const markerElement = document.createElement('button');
      markerElement.className = `incident-marker incident-marker--${incident.severity}`;
      markerElement.type = 'button';
      markerElement.setAttribute('aria-label', `${incident.severity} incident: ${incident.title}`);
      markerElement.innerHTML = '<span></span>';
      markerRef.current = new maplibregl.Marker({ element: markerElement, anchor: 'center' })
        .setLngLat([coordinates[0], coordinates[1]])
        .addTo(mapRef.current);
      mapRef.current.easeTo({ center: [coordinates[0], coordinates[1]], zoom: 14.6, duration: 700 });
    });
  }, [incident, mapReady]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    let cancelled = false;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (cancelled || !mapRef.current) return;
      observationMarkersRef.current.forEach(marker => marker.remove());
      observationMarkersRef.current = [];

      const current = observations.filter(item => !item.qualityFlags.includes('stale'));
      for (const observation of current
        .filter(item => item.geometryGeojson?.type === 'Point' && item.dataClassification !== 'reference')
        .slice(0, 80)) {
        const coordinates = observation.geometryGeojson?.coordinates;
        if (!Array.isArray(coordinates) || coordinates.length < 2) continue;
        const element = document.createElement('button');
        const isTransit = observation.sourceCode.includes('transit');
        element.className = `source-map-marker ${isTransit ? 'source-map-marker--transit' : 'source-map-marker--traffic'}`;
        element.type = 'button';
        element.title = `${observation.sourceName}: ${observation.summary}`;
        element.setAttribute('aria-label', `${observation.sourceName}: ${observation.summary}`);
        element.innerHTML = `<span>${isTransit ? 'T' : 'V'}</span>`;
        observationMarkersRef.current.push(
          new maplibregl.Marker({ element, anchor: 'center' })
            .setLngLat([Number(coordinates[0]), Number(coordinates[1])])
            .addTo(map),
        );
      }

      const featureCollection = {
        type: 'FeatureCollection',
        features: current
          .filter(item => ['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'].includes(String(item.geometryGeojson?.type)))
          .map(item => ({ type: 'Feature', id: item.evidenceId, properties: { source: item.sourceName, summary: item.summary }, geometry: item.geometryGeojson })),
      };
      const existingSource = map.getSource('authoritative-operations') as { setData: (data: unknown) => void } | undefined;
      if (existingSource) existingSource.setData(featureCollection);
      else {
        map.addSource('authoritative-operations', { type: 'geojson', data: featureCollection as never });
        map.addLayer({ id: 'authoritative-operations-glow', type: 'line', source: 'authoritative-operations', paint: { 'line-color': '#c9a66b', 'line-width': 4, 'line-opacity': 0.18, 'line-blur': 2 } });
        map.addLayer({ id: 'authoritative-operations-line', type: 'line', source: 'authoritative-operations', paint: { 'line-color': '#c9a66b', 'line-width': 2, 'line-opacity': 0.9 } });
      }
    });
    return () => { cancelled = true; };
  }, [mapReady, observations]);

  useEffect(() => {
    let cancelled = false;
    void operationalApi.referenceLayers()
      .then(catalog => { if (!cancelled) setReferenceCatalog(catalog); })
      .catch(() => { if (!cancelled) setReferenceCatalog([]); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    let cancelled = false;

    for (const definition of referenceCatalog) {
      if (activeReference.includes(definition.code)) continue;
      const sourceId = `reference-${definition.code}`;
      for (const suffix of ['point', 'fill', 'line']) {
        if (map.getLayer(`${sourceId}-${suffix}`)) map.removeLayer(`${sourceId}-${suffix}`);
      }
      if (map.getSource(sourceId)) map.removeSource(sourceId);
    }

    void (async () => {
      for (const code of activeReference) {
        const definition = referenceCatalog.find(item => item.code === code);
        const sourceId = `reference-${code}`;
        if (!definition || map.getSource(sourceId)) continue;
        try {
          const layer = referenceCacheRef.current.get(code) ?? await operationalApi.referenceLayer(code);
          referenceCacheRef.current.set(code, layer);
          if (cancelled || map.getSource(sourceId)) continue;
          map.addSource(sourceId, { type: 'geojson', data: layer.featureCollection as never });
          if (definition.geometryType === 'point') {
            map.addLayer({
              id: `${sourceId}-point`, type: 'circle', source: sourceId,
              paint: { 'circle-radius': 3.5, 'circle-color': '#7eb8c9', 'circle-opacity': 0.7, 'circle-stroke-width': 1, 'circle-stroke-color': '#0a0c0e' },
            });
          } else {
            map.addLayer({
              id: `${sourceId}-fill`, type: 'fill', source: sourceId,
              paint: { 'fill-color': '#5dcaa5', 'fill-opacity': 0.18 },
            });
            map.addLayer({
              id: `${sourceId}-line`, type: 'line', source: sourceId,
              paint: { 'line-color': '#8fd9bd', 'line-width': 1, 'line-opacity': 0.55 },
            });
          }
        } catch {
          if (!cancelled) setReferenceError(`${definition.name} is unavailable from City GIS`);
        }
      }
    })();

    return () => { cancelled = true; };
  }, [mapReady, activeReference, referenceCatalog]);

  const delayed = sources.filter(source => source.status !== 'healthy' || source.connectionStatus !== 'connected');
  const liveObservations = observations.filter(item => item.dataClassification !== 'reference' && !item.qualityFlags.includes('stale'));
  const closureCount = liveObservations.filter(item => item.sourceCode.includes('closure')).length;
  const transitCount = liveObservations.filter(item => item.sourceCode.includes('transit')).length;
  return (
    <section className="map-pane" aria-label="Operational map">
      <div ref={containerRef} className="map-canvas" />
      <div className="map-toolbar">
        <span>{liveObservations.length} live · {transitCount} transit · {closureCount} closures</span>
        {referenceCatalog.map(definition => {
          const active = activeReference.includes(definition.code);
          return (
            <button
              key={definition.code}
              type="button"
              className={active ? 'map-reference__toggle map-reference__toggle--on' : 'map-reference__toggle'}
              aria-pressed={active}
              title={definition.limitations}
              onClick={() => {
                setReferenceError(null);
                setActiveReference(current => active
                  ? current.filter(code => code !== definition.code)
                  : [...current, definition.code]);
              }}
            >
              {definition.name}
            </button>
          );
        })}
        {referenceError && <small>{referenceError}</small>}
      </div>
      {delayed.length > 0 && <span className="map-toolbar__note">{delayed.length} feeds need attention — listed at right</span>}
    </section>
  );
}

function ApprovalProgress({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="approval-progress">
      {recommendation.approvalRequirements.map(requirement => (
        <div key={requirement.requirementId} className={`approval-party approval-party--${requirement.status}`}>
          <span>{requirement.status === 'satisfied' ? 'Approved' : 'Pending'}</span>
          <strong>{requirement.agencyName}</strong>
          <small>{titleCase(requirement.roleCode)}</small>
        </div>
      ))}
    </div>
  );
}

function deskNote(finding: AgentFinding): string {
  const text = finding.status === 'contributed' ? finding.interpretation : finding.observation;
  return text.length > 92 ? `${text.slice(0, 89).trim()}…` : text;
}

function DeskBoard({
  findings, canConfigure, onConfigureDesk,
}: {
  findings: AgentFinding[];
  canConfigure: boolean;
  onConfigureDesk: (deskCode: 'atlas' | 'aqua') => void;
}) {
  const nexus = findings.find(finding => finding.agentCode === 'nexus');
  const byCode = new Map(findings.map(finding => [finding.agentCode.toLowerCase(), finding]));
  return (
    <section className="desk-board" aria-label="Agent desks">
      <header className="desk-board__head">
        <div>
          <span>Who looked</span>
          <strong>Six desks</strong>
        </div>
        {nexus && <p>{nexus.observation}</p>}
      </header>
      <div className="desk-board__grid" role="list">
        {DESK_ORDER.map(code => {
          const finding = byCode.get(code);
          const status = finding?.status ?? 'absent';
          return (
            <article
              key={code}
              className={`desk-card desk-card--${status}${finding?.conflicts.length ? ' desk-card--dissent' : ''}`}
              role="listitem"
            >
              <img src={DESK_ICONS[code]} alt="" />
              <div className="desk-card__brand">
                <strong>{deskCallsign(code)}</strong>
                <small>{deskName(code)}</small>
              </div>
              <span className="desk-card__status">{finding ? deskStatusLabel(finding.status, finding.modelVersion) : 'Not on this card'}</span>
              <p>{finding ? deskNote(finding) : 'This desk was not asked to review this incident.'}</p>
              {(code === 'atlas' || code === 'aqua') && canConfigure && (
                <button type="button" className="desk-card__config" onClick={() => onConfigureDesk(code)}>Configure</button>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ReviewWorkspace({
  incident, recommendation, map, onReview, canConfigure, onConfigureDesk,
}: {
  incident: Incident | null;
  recommendation: Recommendation | null;
  map: ReactNode;
  onReview: (action: DecisionAction) => void;
  canConfigure: boolean;
  onConfigureDesk: (deskCode: 'atlas' | 'aqua') => void;
}) {
  if (!incident) {
    return (
      <section className="workspace-pane workspace-pane--empty">
        <div className="empty-state">
          <span className="empty-state__code">Clear</span>
          <h2>No incident to review</h2>
          <p>The map still shows the operating area. A decision card will open here when a rule fires.</p>
        </div>
        <div className="workspace-map">{map}</div>
      </section>
    );
  }

  return (
    <section className="workspace-pane" aria-label="Review workspace">
      <header className="workspace-head">
        <span>Review this</span>
        <h1>{incident.title}</h1>
        <div className="workspace-head__meta">
          <span className={`severity-badge severity-badge--${incident.severity}`}>{severityLabel(incident.severity)}</span>
          {recommendation && <span>Decide by {formatTime(recommendation.expiresAt)}</span>}
          <span>Owned by {incident.commandOwner?.displayName || 'Unassigned'}</span>
        </div>
      </header>
      <div className="workspace-map">{map}</div>
      {recommendation?.agentFindings?.length
        ? <DeskBoard findings={recommendation.agentFindings} canConfigure={canConfigure} onConfigureDesk={onConfigureDesk} />
        : (
          <section className="desk-board">
            <header className="desk-board__head"><div><span>Who looked</span><strong>Six desks</strong></div></header>
            <p className="desk-board__empty">Desks have not reviewed this yet.</p>
          </section>
        )}
      <div className="workspace-cards">
        <article className="workspace-card">
          <span>Why this is open</span>
          <p>{incident.whatChanged}</p>
          <p className="workspace-card__muted">{incident.whyItMatters}</p>
        </article>
        <article className="workspace-card workspace-card--action">
          <span>Recommended next step</span>
          <p>{recommendation?.recommendedAction || 'No recommendation is waiting.'}</p>
          {recommendation && <p className="workspace-card__muted">{recommendation.expectedEffect}</p>}
        </article>
        <article className="workspace-card workspace-card--limits">
          <span>Limits</span>
          {recommendation ? (
            <>
              <ul>{recommendation.constraints.map(constraint => <li key={constraint}>{constraint}</li>)}</ul>
              <p className="workspace-card__muted">{recommendation.limitations}</p>
            </>
          ) : <p>No decision limits are open.</p>}
        </article>
      </div>
      {recommendation && (
        <div className="workspace-actions">
          <button className="button button--danger" type="button" onClick={() => onReview('reject')}>Decline</button>
          <button className="button button--secondary" type="button" onClick={() => onReview('request_revision')}>Send back</button>
          <button className="button button--secondary" type="button" onClick={() => onReview('escalate')}>Escalate</button>
          <button className="button button--approve" type="button" onClick={() => onReview('approve')}>Approve</button>
        </div>
      )}
    </section>
  );
}

function ReviewContext({
  recommendation, sources, commitments, onTransition,
}: {
  recommendation: Recommendation | null;
  sources: SourceHealth[];
  commitments: Commitment[];
  onTransition: (commitment: Commitment, target: Commitment['state']) => void;
}) {
  const attention = sources.filter(source => source.status !== 'healthy' || source.connectionStatus !== 'connected');
  return (
    <aside className="context-pane" aria-label="Sources and assigned work">
      <section className="context-card">
        <header className="pane-head">
          <div>
            <span>Official sources</span>
            <strong>{recommendation?.evidence.length ?? 0} cited</strong>
          </div>
        </header>
        {recommendation?.evidence.length ? recommendation.evidence.map(item => (
          <div className="evidence-line" key={item.evidenceId}>
            <div><strong>{item.sourceName}</strong><small>{formatTime(item.observedAt)}</small></div>
            <p>{item.summary}</p>
          </div>
        )) : <p className="context-empty">No cited sources until a recommendation is open.</p>}
        {recommendation && <ApprovalProgress recommendation={recommendation} />}
      </section>
      <section className="context-card">
        <header className="pane-head">
          <div>
            <span>Feeds</span>
            <strong className={attention.length ? 'text-warning' : 'text-positive'}>
              {attention.length ? `${attention.length} need attention` : 'All connected'}
            </strong>
          </div>
        </header>
        <div className="context-feeds">
          {sources.map(source => (
            <div key={source.sourceId} className={`source-item source-item--${source.connectionStatus || 'not_connected'}`}>
              <StatusDot status={source.status} />
              <div>
                <strong>{source.name}</strong>
                <span>{source.connectionStatus && source.connectionStatus !== 'connected' ? connectionLabel(source.connectionStatus) : formatRelativeSeconds(source.lagSeconds)}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className="context-card">
        <header className="pane-head">
          <div>
            <span>Assigned work</span>
            <strong>{commitments.length} open</strong>
          </div>
        </header>
        {commitments.length === 0 ? (
          <p className="context-empty">Approving creates a record of who is responsible. It does not send a crew.</p>
        ) : commitments.map(commitment => (
          <article key={commitment.commitmentId} className={`commitment-card commitment-card--${commitment.state}`}>
            <div className="commitment-card__status"><span />{commitmentLabel(commitment.state)}</div>
            <h3>{commitment.ownerAgencyName}</h3>
            <p>{commitment.requestedOutcome}</p>
            {commitment.state === 'requested' && (
              <button type="button" onClick={() => onTransition(commitment, 'acknowledged')}>Mark seen</button>
            )}
            {commitment.state === 'acknowledged' && (
              <button type="button" onClick={() => onTransition(commitment, 'approved')}>Accept</button>
            )}
            {commitment.state === 'approved' && (
              <button type="button" onClick={() => onTransition(commitment, 'executing')}>Mark in progress</button>
            )}
          </article>
        ))}
      </section>
    </aside>
  );
}

function DecisionDialog({
  state,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  state: DecisionDialogState;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (reason: string, comment: string) => void;
}) {
  const [reason, setReason] = useState(state.action === 'approve' ? 'EVIDENCE_AND_CONSTRAINTS_REVIEWED' : '');
  const [comment, setComment] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const requiresComment = state.action !== 'approve';
  const ready = confirmed && reason.trim().length >= 2 && (!requiresComment || comment.trim().length >= 4);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={event => { if (event.currentTarget === event.target && !busy) onClose(); }}>
      <section className="decision-dialog" role="dialog" aria-modal="true" aria-labelledby="decision-dialog-title">
        <div className="decision-dialog__header">
          <div>
            <span>Your decision</span>
            <h2 id="decision-dialog-title">{decisionLabels[state.action]}</h2>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close decision review">Close</button>
        </div>
        <div className="dialog-summary">
          <span>Recommendation version {state.recommendation.version}</span>
          <p>{state.recommendation.recommendedAction}</p>
        </div>
        <div className="dialog-grid">
          <div><span>Evidence snapshot</span><strong>{state.recommendation.evidenceSnapshotHash}</strong></div>
          <div><span>Decide by</span><strong>{formatTime(state.recommendation.expiresAt)}</strong></div>
        </div>
        <label className="field-label">
          Why
          <input value={reason} onChange={event => setReason(event.target.value)} placeholder="Short reason" disabled={busy} />
        </label>
        <label className="field-label">
          Note {requiresComment ? '(required)' : '(optional)'}
          <textarea value={comment} onChange={event => setComment(event.target.value)} placeholder="What you reviewed and any limits that still apply." disabled={busy} />
        </label>
        <label className="confirmation-check">
          <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} />
          <span>I reviewed the cited sources, any feed warnings, and the limits listed on the card. Approving records who is responsible. It does not control a signal, radio, or field system.</span>
        </label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="decision-dialog__actions">
          <button className="button button--secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button className={state.action === 'approve' ? 'button button--approve' : 'button button--danger'} type="button" disabled={!ready || busy} onClick={() => onSubmit(reason, comment)}>
            {busy ? 'Recording decision…' : decisionLabels[state.action]}
          </button>
        </div>
      </section>
    </div>
  );
}

function LoadingScreen() {
  return <main className="system-screen"><div className="system-panel"><div className="loading-mark" /><h1>Opening the desk</h1><p>Checking who you are, the open window, and which feeds are connected.</p></div></main>;
}

function FailureScreen({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <main className="system-screen">
      <div className="system-panel system-panel--error">
        <span className="system-code">Unavailable</span>
        <h1>{title}</h1><p>{message}</p>
        <button className="button button--approve" type="button" onClick={onRetry}>Try again</button>
      </div>
    </main>
  );
}

export function OperationalCommandCenter() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [principal, setPrincipal] = useState<PrincipalContext | null>(null);
  const [snapshot, setSnapshot] = useState<OperationalSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DecisionDialogState | null>(null);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [packs, setPacks] = useState<ScenarioPack[]>([]);
  const [noActiveWindow, setNoActiveWindow] = useState(false);
  const [windowDialogOpen, setWindowDialogOpen] = useState(false);
  const [configDesk, setConfigDesk] = useState<'atlas' | 'aqua' | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextStatus, nextPrincipal] = await Promise.all([operationalApi.status(), operationalApi.principal()]);
      setStatus(nextStatus);
      setPrincipal(nextPrincipal);
      operationalApi.scenarioPacks().then(setPacks).catch(() => setPacks([]));

      let event;
      try {
        event = await operationalApi.activeEvent();
      } catch (reason) {
        if (reason instanceof OperationalApiError && reason.code === 'NO_ACTIVE_EVENT') {
          setNoActiveWindow(true);
          setSnapshot(null);
          setError(null);
          return;
        }
        throw reason;
      }

      const nextSnapshot = await operationalApi.snapshot(event.eventId);
      setNoActiveWindow(false);
      setSnapshot(nextSnapshot);
      setError(null);
    } catch (reason) {
      if (reason instanceof OperationalApiError && (reason.status === 401 || reason.code === 'IDENTITY_CLAIMS_INCOMPLETE')) {
        window.location.assign('/');
        return;
      }
      const message = reason instanceof OperationalApiError ? reason.message : 'Unable to open the desk.';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!snapshot?.event.eventId) return;
    const timer = window.setInterval(() => void load(true), 15_000);
    return () => { window.clearInterval(timer); };
  }, [load, snapshot?.event.eventId]);

  const openIncidents = useMemo(
    () => snapshot?.incidents.filter(item => ['active', 'triaged', 'new'].includes(item.status)) || snapshot?.incidents || [],
    [snapshot],
  );
  const selectedIncident = openIncidents.find(item => item.incidentId === selectedIncidentId) || openIncidents[0] || null;
  const selectedRecommendation = snapshot?.decisionQueue.find(item => item.incidentId === selectedIncident?.incidentId) || snapshot?.decisionQueue[0] || null;

  const submitDecision = async (reason: string, comment: string) => {
    if (!dialog) return;
    setMutationBusy(true); setMutationError(null);
    try {
      await operationalApi.decide(dialog.recommendation, dialog.action, reason, comment || undefined);
      setDialog(null);
      await load(true);
    } catch (reasonValue) {
      setMutationError(reasonValue instanceof OperationalApiError ? reasonValue.message : 'Decision could not be recorded.');
    } finally {
      setMutationBusy(false);
    }
  };

  const transitionCommitment = async (commitment: Commitment, target: Commitment['state']) => {
    try {
      await operationalApi.transitionCommitment(commitment, target, `OPERATOR_${target.toUpperCase()}`, `Transition recorded by ${principal?.displayName || 'operator'}.`);
      await load(true);
    } catch (reason) {
      setError(reason instanceof OperationalApiError ? reason.message : 'Commitment transition failed.');
    }
  };

  const canManageWindow = Boolean(principal?.scopes.includes('event:manage'));

  const openWindow = async (input: { packCode: string; name: string; locationName: string }) => {
    setMutationBusy(true); setMutationError(null);
    try {
      await operationalApi.openOperatingWindow(input);
      setWindowDialogOpen(false);
      await load();
    } catch (reason) {
      setMutationError(reason instanceof OperationalApiError ? reason.message : 'The operating window could not be opened.');
    } finally {
      setMutationBusy(false);
    }
  };

  const closeWindow = async () => {
    if (!snapshot) return;
    const confirmed = window.confirm(
      'Close this operating window? Detection stops, open incidents are closed, and pending recommendations expire.',
    );
    if (!confirmed) return;
    try {
      await operationalApi.closeOperatingWindow(snapshot.event.eventId);
      await load();
    } catch (reason) {
      setError(reason instanceof OperationalApiError ? reason.message : 'The operating window could not be closed.');
    }
  };

  const windowDialog = windowDialogOpen && (
    <OperatingWindowDialog
      packs={packs}
      busy={mutationBusy}
      error={mutationError}
      onClose={() => { setWindowDialogOpen(false); setMutationError(null); }}
      onOpen={input => void openWindow(input)}
    />
  );

  if (loading && !snapshot) return <LoadingScreen />;
  if (noActiveWindow && !snapshot) {
    return (
      <>
        <NoOperatingWindowScreen
          canManage={canManageWindow}
          onOpenWindow={() => setWindowDialogOpen(true)}
          onRetry={() => void load()}
        />
        {windowDialog}
      </>
    );
  }
  if (error && !snapshot) return <FailureScreen title="The desk could not open" message={error} onRetry={() => void load()} />;
  if (!status || !principal || !snapshot) return <FailureScreen title="Nothing is being coordinated" message="An open window, a working database, and a named operator are required." onRetry={() => void load()} />;

  const activePack = packs.find(pack => pack.packCode === snapshot.event.scenarioPackCode) ?? null;

  return (
    <div className="command-shell desk">
      <Header
        status={status}
        snapshot={snapshot}
        principal={principal}
        pack={activePack}
        canManage={canManageWindow}
        onChangeWindow={() => { setMutationError(null); setWindowDialogOpen(true); }}
        onCloseWindow={() => void closeWindow()}
      />
      <main className="desk-layout">
        <ReviewQueue
          incidents={openIncidents}
          selectedId={selectedIncident?.incidentId ?? null}
          recommendation={selectedRecommendation}
          onSelect={setSelectedIncidentId}
        />
        <ReviewWorkspace
          incident={selectedIncident}
          recommendation={selectedRecommendation}
          map={<OperationalMap incident={selectedIncident} sources={snapshot.sources} observations={snapshot.observations} />}
          canConfigure={canManageWindow}
          onConfigureDesk={deskCode => { setMutationError(null); setConfigDesk(deskCode); }}
          onReview={action => {
            if (selectedRecommendation) {
              setMutationError(null);
              setDialog({ action, recommendation: selectedRecommendation });
            }
          }}
        />
        <ReviewContext
          recommendation={selectedRecommendation}
          sources={snapshot.sources}
          commitments={snapshot.commitments}
          onTransition={transitionCommitment}
        />
      </main>
      {error && <button className="global-alert" type="button" onClick={() => setError(null)}>{error}<span>Dismiss</span></button>}
      {dialog && <DecisionDialog state={dialog} busy={mutationBusy} error={mutationError} onClose={() => setDialog(null)} onSubmit={submitDecision} />}
      {windowDialog}
      {configDesk && (
        <DeskConfigDialog
          deskCode={configDesk}
          busy={mutationBusy}
          error={mutationError}
          onClose={() => { setConfigDesk(null); setMutationError(null); }}
          onSaved={async () => {
            await load(true);
            setConfigDesk(null);
          }}
        />
      )}
    </div>
  );
}
