import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { NoOperatingWindowScreen, OperatingWindowChip, OperatingWindowDialog } from './OperatingWindow';

type DecisionAction = 'approve' | 'reject' | 'request_revision' | 'escalate';

interface DecisionDialogState {
  action: DecisionAction;
  recommendation: Recommendation;
}

const decisionLabels: Record<DecisionAction, string> = {
  approve: 'Approve recommendation',
  reject: 'Reject recommendation',
  request_revision: 'Request revision',
  escalate: 'Escalate decision',
};

function formatTime(value: string | null): string {
  if (!value) return 'Not available';
  return new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
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
      <div className={`mode-chip ${isReview ? 'mode-chip--review' : 'mode-chip--live'}`}>
        <span className="mode-chip__dot" />
        <span>{isReview ? 'LOCAL REVIEW DATA' : 'LIVE OPERATIONS'}</span>
      </div>
      <div className="brand-lockup">
        <img src="/harbert-logo.jpg" alt="Auburn University Harbert College of Business" />
        <div>
          <strong>Nexus Coordinate</strong>
          <span>{event.name}</span>
        </div>
      </div>
      <OperatingWindowChip
        event={event}
        pack={pack}
        canManage={canManage}
        onChange={onChangeWindow}
        onClose={onCloseWindow}
      />
      <div className="header-field">
        <span>Phase</span>
        <strong>{titleCase(event.phase)}</strong>
      </div>
      <div className="header-field header-field--wide">
        <span>Command owner</span>
        <strong>{event.commandOwner?.displayName || 'Unassigned'} · {event.commandOwner?.agencyName || 'No agency'}</strong>
      </div>
      <div className="header-field">
        <span>Data connections</span>
        <strong className={sourcesRequiringAction ? 'text-warning' : 'text-positive'}>
          {connectedSources} connected · {sourcesRequiringAction} require action
        </strong>
      </div>
      <div className="operator-menu" aria-label="Signed in operator">
        <span>{principal.displayName}</span>
        <strong>{principal.agencyName}</strong>
      </div>
    </header>
  );
}

function SituationBrief({ incident, recommendation }: { incident: Incident | null; recommendation: Recommendation | null }) {
  if (!incident) {
    return (
      <section className="situation-pane empty-pane">
        <div className="empty-state">
          <span className="empty-state__code">CLEAR</span>
          <h2>No active operational incident</h2>
          <p>Nexus will surface verified changes here when evidence crosses an approved event threshold.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="situation-pane" aria-label="Current situation brief">
      <div className="section-eyebrow">Current situation</div>
      <div className="severity-line">
        <span className={`severity-badge severity-badge--${incident.severity}`}>{incident.severity}</span>
        <span>Detected {formatTime(incident.detectedAt)}</span>
      </div>
      <h1>{incident.title}</h1>
      <div className="brief-block">
        <span>What changed</span>
        <p>{incident.whatChanged}</p>
      </div>
      <div className="brief-block brief-block--risk">
        <span>Why it matters</span>
        <p>{incident.whyItMatters}</p>
      </div>
      <div className="brief-block brief-block--next">
        <span>Next decision</span>
        <p>{recommendation ? `Review required by ${recommendation.expiresAt ? formatTime(recommendation.expiresAt) : 'the assigned authority'}.` : 'No human decision is currently pending.'}</p>
      </div>
      <div className="tag-group" aria-label="Affected services">
        {incident.affectedServices.map(service => <span key={service}>{service}</span>)}
      </div>
      <div className="owner-block">
        <span>Incident owner</span>
        <strong>{incident.commandOwner?.displayName || 'Unassigned'}</strong>
        <small>{incident.commandOwner?.agencyName || 'No agency assigned'}</small>
      </div>
    </section>
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
            paint: { 'raster-brightness-max': 0.72, 'raster-brightness-min': 0.03, 'raster-contrast': 0.18, 'raster-saturation': -0.22 },
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
    });
    return () => {
      cancelled = true;
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
      markerElement.innerHTML = '<span></span><strong>ACTIVE INCIDENT</strong>';
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
        map.addLayer({ id: 'authoritative-operations-glow', type: 'line', source: 'authoritative-operations', paint: { 'line-color': '#ff9f43', 'line-width': 8, 'line-opacity': 0.28, 'line-blur': 5 } });
        map.addLayer({ id: 'authoritative-operations-line', type: 'line', source: 'authoritative-operations', paint: { 'line-color': '#ffb45f', 'line-width': 3.5, 'line-opacity': 0.95 } });
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
              paint: { 'circle-radius': 4, 'circle-color': '#54d6ff', 'circle-opacity': 0.75, 'circle-stroke-width': 1, 'circle-stroke-color': '#04222c' },
            });
          } else {
            map.addLayer({
              id: `${sourceId}-fill`, type: 'fill', source: sourceId,
              paint: { 'fill-color': '#8fd6a0', 'fill-opacity': 0.35 },
            });
            map.addLayer({
              id: `${sourceId}-line`, type: 'line', source: sourceId,
              paint: { 'line-color': '#b7f0c4', 'line-width': 1, 'line-opacity': 0.7 },
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
      <div className="map-titlebar">
        <div>
          <span>Operational map</span>
          <strong>{incident?.title || 'Auburn event area'}</strong>
        </div>
        <div className="map-freshness">
          <span>{liveObservations.length} authoritative observations</span>
          <span>{transitCount} active transit vehicles</span>
          <span>{closureCount} current closures / detours</span>
        </div>
      </div>
      <div className="map-legend">
        <span><i className="legend-dot legend-dot--incident" /> Active incident</span>
        <span><i className="legend-line" /> Official closure / detour</span>
        <span><i className="legend-dot legend-dot--capacity" /> Live transit / flow observation</span>
      </div>
      {referenceCatalog.length > 0 && (
        <div className="map-reference" role="group" aria-label="City reference layers">
          <span>City asset reference</span>
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
      )}
      {delayed.length > 0 && (
        <button className="source-warning" type="button" aria-label="Open source health">
          <strong>{delayed.length} source{delayed.length === 1 ? '' : 's'} require connection or attention</strong>
          <span>{delayed.map(source => source.name).join(', ')}</span>
        </button>
      )}
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

function DeskReview({ findings }: { findings: AgentFinding[] }) {
  if (!findings.length) return null;
  const nexus = findings.find(finding => finding.agentCode === 'nexus');
  const desks = findings.filter(finding => finding.agentCode !== 'nexus');
  return (
    <div className="decision-block decision-block--desks">
      <span>Agent desks</span>
      {nexus && (
        <p className="desk-composition">
          {nexus.observation} {nexus.interpretation}
        </p>
      )}
      {nexus?.limitations && <p className="desk-composition__limit">{nexus.limitations}</p>}
      <div className="desk-chips" role="list">
        {desks.map(finding => (
          <article
            key={finding.agentCode}
            className={`desk-chip desk-chip--${finding.status}${finding.conflicts.length ? ' desk-chip--dissent' : ''}`}
            role="listitem"
          >
            <header>
              <strong>{finding.agentName}</strong>
              <span>{finding.status === 'contributed' ? 'Contributed' : 'No evidence'}</span>
            </header>
            <p>{finding.status === 'contributed' ? finding.interpretation : finding.observation}</p>
            {finding.conflicts.map(conflict => (
              <small key={`${conflict.withAgentCode}:${conflict.concern}`}>
                Disagrees with {conflict.withAgentCode.toUpperCase()}: {conflict.concern}
              </small>
            ))}
          </article>
        ))}
      </div>
    </div>
  );
}

function DecisionQueue({ recommendation, onReview }: { recommendation: Recommendation | null; onReview: (action: DecisionAction) => void }) {
  if (!recommendation) {
    return (
      <aside className="decision-pane empty-pane">
        <div className="section-eyebrow">Decision queue</div>
        <div className="empty-state">
          <span className="empty-state__code">0 PENDING</span>
          <h2>No decision requires your authority</h2>
          <p>Approved agency commitments remain visible in the action rail below.</p>
        </div>
      </aside>
    );
  }

  return (
    <aside className="decision-pane" aria-label="Human decision queue">
      <div className="decision-pane__header">
        <div>
          <span className="section-eyebrow">Decision queue · 1 requires authority</span>
          <h2>{recommendation.priority.toUpperCase()} PRIORITY</h2>
        </div>
        <div className="expiry-chip">
          <span>Expires</span>
          <strong>{formatTime(recommendation.expiresAt)}</strong>
        </div>
      </div>
      <div className="decision-scroll">
        <div className="decision-block decision-block--action">
          <span>AI-assisted recommendation</span>
          <p>{recommendation.recommendedAction}</p>
        </div>
        <DeskReview findings={recommendation.agentFindings ?? []} />
        <div className="decision-block">
          <span>Expected effect</span>
          <p>{recommendation.expectedEffect}</p>
        </div>
        <div className="decision-block decision-block--constraints">
          <span>Operational constraints</span>
          <ul>{recommendation.constraints.map(constraint => <li key={constraint}>{constraint}</li>)}</ul>
        </div>
        <div className="decision-block">
          <span>Evidence reviewed by Nexus</span>
          {recommendation.evidence.map(item => (
            <div className="evidence-line" key={item.evidenceId}>
              <div><strong>{item.sourceName}</strong><small>{formatTime(item.observedAt)}</small></div>
              <p>{item.summary}</p>
            </div>
          ))}
        </div>
        <div className="decision-block decision-block--limitations">
          <span>Known limitation</span>
          <p>{recommendation.limitations}</p>
        </div>
        <ApprovalProgress recommendation={recommendation} />
      </div>
      <div className="decision-actions">
        <button className="button button--approve" type="button" onClick={() => onReview('approve')}>Review & approve</button>
        <button className="button button--secondary" type="button" onClick={() => onReview('request_revision')}>Revise</button>
        <button className="button button--danger" type="button" onClick={() => onReview('reject')}>Reject</button>
        <button className="button button--secondary" type="button" onClick={() => onReview('escalate')}>Escalate</button>
      </div>
    </aside>
  );
}

function CommitmentRail({ commitments, onTransition }: { commitments: Commitment[]; onTransition: (commitment: Commitment, target: Commitment['state']) => void }) {
  return (
    <section className="commitment-rail" aria-label="Persistent agency commitment rail">
      <div className="commitment-rail__title">
        <span>Agency commitments</span>
        <strong>{commitments.length} active</strong>
      </div>
      <div className="commitment-list">
        {commitments.length === 0 ? (
          <div className="commitment-empty">No agency commitment has been created. Approval creates accountable work; it does not execute an agency action.</div>
        ) : commitments.map(commitment => (
          <article key={commitment.commitmentId} className={`commitment-card commitment-card--${commitment.state}`}>
            <div className="commitment-card__status"><span />{titleCase(commitment.state)}</div>
            <h3>{commitment.ownerAgencyName}</h3>
            <p>{commitment.requestedOutcome}</p>
            <div className="commitment-card__meta">
              <span>Due {formatTime(commitment.dueAt)}</span>
              <span>v{commitment.version}</span>
            </div>
            {commitment.state === 'requested' && (
              <button type="button" onClick={() => onTransition(commitment, 'acknowledged')}>Acknowledge request</button>
            )}
            {commitment.state === 'acknowledged' && (
              <button type="button" onClick={() => onTransition(commitment, 'approved')}>Accept work</button>
            )}
            {commitment.state === 'approved' && (
              <button type="button" onClick={() => onTransition(commitment, 'executing')}>Mark executing</button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function SourceStrip({ sources }: { sources: SourceHealth[] }) {
  return (
    <div className="source-strip" aria-label="Source freshness">
      {sources.map(source => (
        <div key={source.sourceId} className={`source-item source-item--${source.connectionStatus || 'not_connected'}`} title={source.authorityUri || source.ownerAgencyName}>
          <StatusDot status={source.status} />
          <div><strong>{source.name}</strong><span>{source.connectionStatus && source.connectionStatus !== 'connected' ? titleCase(source.connectionStatus) : formatRelativeSeconds(source.lagSeconds)}</span></div>
          <small>{titleCase(source.dataClassification || 'operational')}</small>
        </div>
      ))}
    </div>
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
            <span>Human authorization</span>
            <h2 id="decision-dialog-title">{decisionLabels[state.action]}</h2>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close decision review">Close</button>
        </div>
        <div className="dialog-summary">
          <span>Exact recommendation version {state.recommendation.version}</span>
          <p>{state.recommendation.recommendedAction}</p>
        </div>
        <div className="dialog-grid">
          <div><span>Evidence snapshot</span><strong>{state.recommendation.evidenceSnapshotHash}</strong></div>
          <div><span>Approval expires</span><strong>{formatTime(state.recommendation.expiresAt)}</strong></div>
        </div>
        <label className="field-label">
          Reason code
          <input value={reason} onChange={event => setReason(event.target.value)} placeholder="Operational reason code" disabled={busy} />
        </label>
        <label className="field-label">
          Operator comment {requiresComment ? '(required)' : '(optional)'}
          <textarea value={comment} onChange={event => setComment(event.target.value)} placeholder="Record the operational rationale and relevant constraints." disabled={busy} />
        </label>
        <label className="confirmation-check">
          <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} />
          <span>I reviewed the cited evidence, degraded-source warning, operational constraints, and exact recommendation version. I understand that approval creates agency commitments and does not directly control an agency system.</span>
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
  return <main className="system-screen"><div className="system-panel"><div className="loading-mark" /><h1>Opening operational command center</h1><p>Validating identity, storage, active event, and source health.</p></div></main>;
}

function FailureScreen({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <main className="system-screen">
      <div className="system-panel system-panel--error">
        <span className="system-code">COMMAND CENTER UNAVAILABLE</span>
        <h1>{title}</h1><p>{message}</p>
        <button className="button button--approve" type="button" onClick={onRetry}>Retry connection</button>
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
      const message = reason instanceof OperationalApiError ? reason.message : 'Unable to open the operational command center.';
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

  const primaryIncident = useMemo(() => snapshot?.incidents.find(item => ['active', 'triaged', 'new'].includes(item.status)) || snapshot?.incidents[0] || null, [snapshot]);
  const primaryRecommendation = snapshot?.decisionQueue[0] || null;

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
  if (error && !snapshot) return <FailureScreen title="Operational services are not ready" message={error} onRetry={() => void load()} />;
  if (!status || !principal || !snapshot) return <FailureScreen title="No live event is available" message="A named operational event, persistent database, and agency identity are required." onRetry={() => void load()} />;

  const activePack = packs.find(pack => pack.packCode === snapshot.event.scenarioPackCode) ?? null;

  return (
    <div className="command-shell">
      <Header
        status={status}
        snapshot={snapshot}
        principal={principal}
        pack={activePack}
        canManage={canManageWindow}
        onChangeWindow={() => { setMutationError(null); setWindowDialogOpen(true); }}
        onCloseWindow={() => void closeWindow()}
      />
      {status.database === 'review_repository' && <div className="review-banner">LOCAL REVIEW DATA · NO LIVE AGENCY SYSTEM IS CONNECTED OR CONTROLLED</div>}
      <main className="command-grid">
        <SituationBrief incident={primaryIncident} recommendation={primaryRecommendation} />
        <OperationalMap incident={primaryIncident} sources={snapshot.sources} observations={snapshot.observations} />
        <DecisionQueue recommendation={primaryRecommendation} onReview={action => { if (primaryRecommendation) { setMutationError(null); setDialog({ action, recommendation: primaryRecommendation }); } }} />
      </main>
      <SourceStrip sources={snapshot.sources} />
      <CommitmentRail commitments={snapshot.commitments} onTransition={transitionCommitment} />
      {error && <button className="global-alert" type="button" onClick={() => setError(null)}>{error}<span>Dismiss</span></button>}
      {dialog && <DecisionDialog state={dialog} busy={mutationBusy} error={mutationError} onClose={() => setDialog(null)} onSubmit={submitDecision} />}
      {windowDialog}
    </div>
  );
}
