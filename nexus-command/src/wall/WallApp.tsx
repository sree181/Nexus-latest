import { useCallback, useEffect, useMemo, useState } from 'react';
import { graphApi } from '../graphApi';
import {
  deskStaffing,
  feedPills,
  impactRows,
  lineageStages,
  liveFeedCount,
  situationFromIncident,
  streetLabel,
  subtitleFromIncident,
  type WallDesk,
  type WallScreen,
} from '../lib/wallSelectors';
import { OperationalApiError, operationalApi } from '../operationalApi';
import type { GraphSnapshot } from '../graphTypes';
import type { OperationalSnapshot, PrincipalContext, SystemStatus } from '../operationalTypes';
import { DecisionLineage } from './DecisionLineage';
import { FunnelBand } from './FunnelBand';
import { OperationsDesk } from './OperationsDesk';
import { SituationHero } from './SituationHero';
import { SlideOver } from './SlideOver';
import { StatusStrip } from './StatusStrip';
import { TopBar } from './TopBar';
import { WallButton } from './WallButton';
import { WallFrame } from './WallFrame';

function formatClock(date: Date): string {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function formatStamp(value: string): string {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function WallApp() {
  const [screen, setScreen] = useState<WallScreen>('operations');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [principal, setPrincipal] = useState<PrincipalContext | null>(null);
  const [snapshot, setSnapshot] = useState<OperationalSnapshot | null>(null);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [clearedAt, setClearedAt] = useState<string | null>(null);
  const [cleared, setCleared] = useState(false);
  const [assignDesk, setAssignDesk] = useState<WallDesk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextStatus, nextPrincipal] = await Promise.all([operationalApi.status(), operationalApi.principal()]);
      setStatus(nextStatus);
      setPrincipal(nextPrincipal);
      let event;
      try {
        event = await operationalApi.activeEvent();
      } catch (reason) {
        if (reason instanceof OperationalApiError && reason.code === 'NO_ACTIVE_EVENT') {
          setSnapshot(null);
          setGraph(null);
          setError(null);
          return;
        }
        throw reason;
      }
      const [nextSnapshot, nextGraph] = await Promise.all([
        operationalApi.snapshot(event.eventId),
        graphApi.snapshot(event.eventId, 'decision_lineage').catch(() => null),
      ]);
      setSnapshot(nextSnapshot);
      setGraph(nextGraph);
      setError(null);
    } catch (reason) {
      setError(reason instanceof OperationalApiError ? reason.message : 'The wall could not open.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!snapshot?.event.eventId) return undefined;
    const timer = window.setInterval(() => void load(true), 15_000);
    return () => window.clearInterval(timer);
  }, [load, snapshot?.event.eventId]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const incident = cleared
    ? null
    : snapshot?.incidents.find(item => ['new', 'triaged', 'active', 'monitoring'].includes(item.status))
      ?? snapshot?.incidents[0]
      ?? null;
  const recommendation = snapshot?.decisionQueue.find(item => item.incidentId === incident?.incidentId)
    ?? snapshot?.decisionQueue[0]
    ?? null;
  const feeds = feedPills(snapshot?.sources ?? [], now.getTime());
  const counts = liveFeedCount(snapshot?.sources ?? []);
  const stages = useMemo(() => lineageStages(graph), [graph]);
  const operator = incident?.commandOwner?.displayName || principal?.displayName || null;
  const desks = deskStaffing(recommendation?.agentFindings ?? [], operator);
  const impact = impactRows(snapshot?.observations ?? [], incident);
  const coordinates = incident?.locationGeojson?.coordinates;
  const center = coordinates && coordinates.length >= 2 ? [coordinates[0], coordinates[1]] as [number, number] : null;
  const priority = !incident ? 'clear' as const : incident.severity === 'medium' ? 'medium' : incident.severity === 'low' || incident.severity === 'informational' ? 'low' : 'high';

  const actions = screen === 'operations' ? [
    { tone: 'primary' as const, label: 'Review now', onPress: () => setScreen('lineage') },
    { tone: 'secondary' as const, label: 'Assign desk', onPress: () => setAssignDesk(desks.find(desk => !desk.staffed) ?? desks[0] ?? null) },
    { tone: 'clear' as const, label: 'Clear', onPress: () => { setCleared(true); setClearedAt(new Date().toISOString()); } },
  ] : [];

  const top = (
    <TopBar
      windowName={snapshot?.event.name || 'Auburn Mobility Operations'}
      feedsLive={counts.live}
      feedsTotal={counts.total}
      feeds={feeds}
      clock={formatClock(now)}
      live={status?.database !== 'review_repository'}
    />
  );
  const statusStrip = (
    <StatusStrip
      active={screen}
      actions={actions}
      onSelect={setScreen}
    />
  );

  if (loading && !snapshot) {
    return (
      <WallFrame
        top={top}
        hero={<SituationHero priority="clear" time={formatClock(now)} owner="" situation="Opening the wall" subtitle="Checking the operating window and official feeds" />}
        body={<div className="wall-system"><h1>Loading</h1><p>Named operator, open window, official feeds.</p></div>}
        status={statusStrip}
      />
    );
  }

  if (error && !snapshot) {
    return (
      <WallFrame
        top={top}
        hero={<SituationHero priority="high" time={formatClock(now)} owner="" situation="The wall could not open" subtitle={error} />}
        body={<div className="wall-system"><WallButton tone="primary" label="Try again" onPress={() => void load()} /></div>}
        status={statusStrip}
      />
    );
  }

  const hero = screen === 'lineage' ? (
    <FunnelBand stages={stages.map(stage => ({ key: stage.key, label: stage.label, count: stage.count }))} />
  ) : (
    <SituationHero
      priority={priority}
      time={incident ? formatStamp(incident.detectedAt) : formatClock(now)}
      owner={operator || ''}
      situation={situationFromIncident(incident)}
      subtitle={screen === 'operations'
        ? subtitleFromIncident(incident, clearedAt)
        : screen === 'coordination'
          ? 'Agency commitments stay on the laptop desk'
          : 'Mobility flow is not on this wall'}
    />
  );

  const body = screen === 'operations' ? (
    <OperationsDesk
      center={center}
      markerLabel={streetLabel(incident)}
      impact={impact}
      desks={desks}
      onAssign={setAssignDesk}
    />
  ) : screen === 'lineage' ? (
    <DecisionLineage stages={stages} />
  ) : (
    <div className="wall-system">
      <h1>{screen === 'coordination' ? 'Agency Coordination' : 'Mobility Flow'}</h1>
      <p>This screen is not on the wall. Use Operations or Lineage.</p>
    </div>
  );

  return (
    <>
      <WallFrame top={top} hero={hero} body={body} status={statusStrip} />
      {assignDesk && (
        <SlideOver
          title={`Assign ${assignDesk.name}`}
          rows={principal ? [{ id: principal.principalId, title: principal.displayName, status: principal.agencyName }] : []}
          onClose={() => setAssignDesk(null)}
          onSelect={() => setAssignDesk(null)}
        />
      )}
    </>
  );
}
