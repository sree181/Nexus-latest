import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash2,
  DatabaseZap,
  GitBranch,
  Layers3,
  Pause,
  Play,
  Radio,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  UserCheck,
} from 'lucide-react';
import './orchestrationPrototype.css';

export interface OrchestrationDeskTask {
  id: string;
  code: string;
  name: string;
  role: string;
  avatar: string;
  logo: string;
  tone: string;
  status: 'Contributed' | 'Dissent' | 'Abstained';
  statusAt: string;
  runtimeKind: 'model' | 'deterministic';
  runtimeLabel: string;
  runtimeDetail: string;
  finding: string;
  note: string;
  evidenceLabel: string;
  boundary: string;
}

export interface OrchestrationPrototypeData {
  runId: string;
  runLabel: string;
  incidentTitle: string;
  incidentIdLine: string;
  recAction: string;
  recState: string;
  recVersion: string;
  recMeta: string;
  snapshotHash: string;
  snapshotState: string;
  evidenceCount: number;
  contributedCount: number;
  abstainedCount: number;
  dissentCount: number;
  commitmentCount: number;
  operatorName: string;
  operatorRole: string;
  awaiting: boolean;
  signed: boolean;
  composeLine: string;
  dissentNote: string;
  tasks: OrchestrationDeskTask[];
}

interface OrchestrationPrototypeProps {
  data: OrchestrationPrototypeData;
}

type Filter = 'all' | 'contributed' | 'abstained' | 'dissent';
type TaskKind = 'evidence' | 'agent' | 'composition' | 'decision';

interface InspectorTask {
  id: string;
  kind: TaskKind;
  phase: number;
  name: string;
  role: string;
  status: string;
  runtime: string;
  detail: string;
  finding: string;
  note: string;
  boundary: string;
  evidence: string;
  tone: string;
  avatar?: string;
  logo?: string;
}

const PHASES = [
  { key: 'snapshot', label: 'Evidence frozen' },
  { key: 'desks', label: 'Desks assess' },
  { key: 'compose', label: 'NEXUS composes' },
  { key: 'decision', label: 'Human decides' },
];

const STATUS_ICON = {
  Contributed: CheckCircle2,
  Dissent: AlertTriangle,
  Abstained: CircleSlash2,
};

function statusClass(status: string): string {
  return status.toLowerCase().replace(/[ _]+/g, '-');
}

function taskIcon(kind: TaskKind) {
  if (kind === 'evidence') return <DatabaseZap aria-hidden="true" />;
  if (kind === 'composition') return <GitBranch aria-hidden="true" />;
  if (kind === 'decision') return <UserCheck aria-hidden="true" />;
  return <ScanLine aria-hidden="true" />;
}

export default function OrchestrationPrototype({ data }: OrchestrationPrototypeProps) {
  const [selectedTaskId, setSelectedTaskId] = useState('evidence-snapshot');
  const [filter, setFilter] = useState<Filter>('all');
  const [followCurrent, setFollowCurrent] = useState(true);
  const [replayPhase, setReplayPhase] = useState(PHASES.length - 1);

  const inspectorTasks = useMemo<InspectorTask[]>(() => {
    const evidence: InspectorTask = {
      id: 'evidence-snapshot',
      kind: 'evidence',
      phase: 0,
      name: 'Evidence snapshot',
      role: 'Immutable input boundary',
      status: data.snapshotState,
      runtime: 'Existing Nexus state',
      detail: `${data.evidenceCount} evidence record${data.evidenceCount === 1 ? '' : 's'}`,
      finding: data.incidentTitle,
      note: `Snapshot ${data.snapshotHash}. The preview reads the current Nexus record; it does not create or replay a trace.`,
      boundary: 'Evidence is presented as frozen input. No prototype interaction changes source records.',
      evidence: `${data.evidenceCount} records · ${data.snapshotHash}`,
      tone: '#55c9a9',
    };

    const agents: InspectorTask[] = data.tasks.map(task => ({
      id: task.id,
      kind: 'agent',
      phase: 1,
      name: task.name,
      role: task.role,
      status: task.status,
      runtime: task.runtimeLabel,
      detail: `${task.runtimeDetail} · ${task.statusAt}`,
      finding: task.finding,
      note: task.note,
      boundary: task.boundary,
      evidence: task.evidenceLabel,
      tone: task.tone,
      avatar: task.avatar,
      logo: task.logo,
    }));

    const composition: InspectorTask = {
      id: 'nexus-composition',
      kind: 'composition',
      phase: 2,
      name: 'NEXUS',
      role: 'Coordinator · composes, never authors',
      status: data.contributedCount > 0 ? 'Composed' : 'Held',
      runtime: 'Deterministic composition',
      detail: `${data.contributedCount} contributed · ${data.abstainedCount} abstained · ${data.dissentCount} dissent`,
      finding: data.composeLine,
      note: data.dissentNote,
      boundary: 'The composed recommendation remains inside the authored playbook and preserves dissent and silence.',
      evidence: `${data.evidenceCount} frozen evidence records`,
      tone: '#f2a33a',
    };

    const decision: InspectorTask = {
      id: 'human-decision',
      kind: 'decision',
      phase: 3,
      name: data.operatorName,
      role: data.operatorRole || 'Named decision stakeholder',
      status: data.signed ? 'Signed' : data.awaiting ? 'Awaiting decision' : data.recState,
      runtime: 'Human accountability gate',
      detail: `Recommendation ${data.recVersion} · ${data.recMeta || data.recState}`,
      finding: data.recAction,
      note: data.commitmentCount > 0
        ? `${data.commitmentCount} commitment record${data.commitmentCount === 1 ? '' : 's'} currently follow the decision.`
        : 'No commitment records exist yet in the current Nexus state.',
      boundary: 'The prototype cannot approve, sign, publish, dispatch, or create commitments.',
      evidence: `Snapshot ${data.snapshotHash}`,
      tone: '#7da9ff',
    };

    return [evidence, ...agents, composition, decision];
  }, [data]);

  const selectedTask = inspectorTasks.find(task => task.id === selectedTaskId) ?? inspectorTasks[0];
  const visiblePhase = followCurrent ? PHASES.length - 1 : replayPhase;
  const currentPhase = PHASES[visiblePhase];

  const filterCounts: Record<Filter, number> = {
    all: data.tasks.length,
    contributed: data.tasks.filter(task => task.status === 'Contributed').length,
    abstained: data.tasks.filter(task => task.status === 'Abstained').length,
    dissent: data.tasks.filter(task => task.status === 'Dissent').length,
  };

  const taskMatchesFilter = (task: OrchestrationDeskTask) => filter === 'all' || task.status.toLowerCase() === filter;
  const phaseState = (phase: number) => phase < visiblePhase ? 'is-complete' : phase === visiblePhase ? 'is-current' : 'is-future';

  const selectTask = (id: string) => setSelectedTaskId(id);
  const enterFollow = () => {
    setFollowCurrent(true);
    setReplayPhase(PHASES.length - 1);
  };
  const enterReplay = () => {
    setFollowCurrent(false);
    setReplayPhase(0);
    setFilter('all');
    setSelectedTaskId('evidence-snapshot');
  };
  const stepReplay = () => {
    const next = replayPhase >= PHASES.length - 1 ? 0 : replayPhase + 1;
    setReplayPhase(next);
    const focusByPhase = ['evidence-snapshot', data.tasks[0]?.id, 'nexus-composition', 'human-decision'];
    setSelectedTaskId(focusByPhase[next] || 'evidence-snapshot');
  };

  return (
    <section className="nx-orchestration" data-screen-label="Orchestration prototype" aria-labelledby="nx-orchestration-title">
      <header className="nx-orchestration__header">
        <div className="nx-orchestration__title-block">
          <div className="nx-orchestration__eyebrow">
            <span>Stage 07 · Orchestration</span>
            <strong><Radio aria-hidden="true" /> UI prototype · existing Nexus state</strong>
          </div>
          <h1 id="nx-orchestration-title">Multi-agent execution preview</h1>
          <p>One operational composition, from frozen evidence through six desk assessments to a named human decision.</p>
        </div>
        <div className="nx-orchestration__truth-note">
          <ShieldCheck aria-hidden="true" />
          <span><strong>No Open Multi-Agent telemetry</strong><small>Trace IDs, token use, provider calls, and task timing are unavailable in this UI-only preview.</small></span>
        </div>
      </header>

      <div className="nx-orchestration__metrics" aria-label="Current Nexus composition summary">
        {[
          { label: 'Evidence', value: data.evidenceCount, note: data.snapshotState, tone: '#55c9a9' },
          { label: 'Contributed', value: data.contributedCount, note: 'recorded desk findings', tone: '#69d9a8' },
          { label: 'Abstained', value: data.abstainedCount, note: 'recorded as silence', tone: '#91a4b6' },
          { label: 'Dissent', value: data.dissentCount, note: 'carried into composition', tone: '#f2a33a' },
          { label: 'Decision', value: data.recVersion, note: data.recState, tone: '#7da9ff' },
        ].map(metric => (
          <article key={metric.label} style={{ '--metric-tone': metric.tone } as React.CSSProperties}>
            <span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.note}</small>
          </article>
        ))}
      </div>

      <div className="nx-orchestration__workspace">
        <aside className="nx-orchestration__runs" aria-label="Run preview rail">
          <header><span>Run preview</span><small>1 current composition</small></header>
          <button
            type="button"
            className="nx-orchestration-run is-selected"
            aria-pressed="true"
            data-orchestration-run={data.runId}
            onClick={() => selectTask('evidence-snapshot')}
          >
            <span className="nx-orchestration-run__state"><i></i>{data.runLabel}</span>
            <strong>{data.incidentTitle}</strong>
            <small>{data.incidentIdLine}</small>
            <dl>
              <div><dt>Recommendation</dt><dd>{data.recVersion}</dd></div>
              <div><dt>State</dt><dd>{data.recState}</dd></div>
            </dl>
          </button>

          <div className="nx-orchestration__empty-run">
            <Layers3 aria-hidden="true" />
            <strong>No additional run records</strong>
            <span>Historical orchestration runs are not exposed by the current Nexus state.</span>
          </div>

          <section className="nx-orchestration__playback" aria-label="Prototype playback controls">
            <header><span>Presentation state</span><small>local UI only</small></header>
            <div role="group" aria-label="Presentation mode">
              <button type="button" aria-pressed={followCurrent} onClick={enterFollow}><Radio aria-hidden="true" />Follow current</button>
              <button type="button" aria-pressed={!followCurrent} onClick={enterReplay}><Pause aria-hidden="true" />Replay view</button>
            </div>
            <div className="nx-orchestration__phase-readout" aria-live="polite">
              <span>Phase {visiblePhase + 1} / {PHASES.length}</span>
              <strong>{currentPhase.label}</strong>
            </div>
            {!followCurrent ? (
              <button type="button" className="nx-orchestration__step" onClick={stepReplay}>
                {replayPhase >= PHASES.length - 1 ? <RotateCcw aria-hidden="true" /> : <Play aria-hidden="true" />}
                {replayPhase >= PHASES.length - 1 ? 'Restart preview' : 'Step forward'}
              </button>
            ) : null}
          </section>

          <footer>
            <span>Trace</span><strong>Unavailable · prototype</strong>
            <span>Source</span><strong>Current Nexus record</strong>
          </footer>
        </aside>

        <main className="nx-orchestration__execution">
          <section className="nx-orchestration__topology" aria-labelledby="nx-topology-title">
            <header>
              <div><span>Execution topology</span><h2 id="nx-topology-title">Evidence → desks → composition → decision</h2></div>
              <div className="nx-orchestration__filters" role="group" aria-label="Filter agent tasks">
                {(['all', 'contributed', 'abstained', 'dissent'] as Filter[]).map(item => (
                  <button key={item} type="button" aria-pressed={filter === item} onClick={() => setFilter(item)}>
                    <span>{item}</span><strong>{filterCounts[item]}</strong>
                  </button>
                ))}
              </div>
            </header>

            <div className="nx-orchestration-graph">
              <div className="nx-orchestration-graph__stages" aria-hidden="true">
                <span>01 · Input</span><span>02 · Parallel assessment</span><span>03 · Synthesis</span><span>04 · Accountability</span>
              </div>
              <svg className="nx-orchestration-graph__links" viewBox="0 0 1200 520" preserveAspectRatio="none" aria-hidden="true">
                {[58, 138, 218, 302, 382, 462].map((y, index) => <path key={`in-${index}`} d={`M 150 260 C 225 260, 215 ${y}, 300 ${y}`} />)}
                {[58, 138, 218, 302, 382, 462].map((y, index) => <path key={`out-${index}`} d={`M 700 ${y} C 790 ${y}, 775 260, 850 260`} />)}
                <path className="is-emphasis" d="M 950 260 C 990 260, 1008 260, 1045 260" />
              </svg>

              <button
                type="button"
                className={`nx-orchestration-node nx-orchestration-node--evidence ${phaseState(0)}${selectedTaskId === 'evidence-snapshot' ? ' is-selected' : ''}`}
                aria-pressed={selectedTaskId === 'evidence-snapshot'}
                onClick={() => selectTask('evidence-snapshot')}
                style={{ '--task-tone': '#55c9a9' } as React.CSSProperties}
              >
                <span className="nx-orchestration-node__icon"><DatabaseZap aria-hidden="true" /></span>
                <span><small>Frozen snapshot</small><strong>{data.evidenceCount} evidence records</strong><em>{data.snapshotHash}</em></span>
              </button>

              <div className="nx-orchestration-graph__agents" aria-label="Six agent assessment tasks">
                {data.tasks.map(task => {
                  const StatusIcon = STATUS_ICON[task.status];
                  const matches = taskMatchesFilter(task);
                  return (
                    <button
                      key={task.id}
                      type="button"
                      data-orchestration-task={task.code}
                      className={`nx-orchestration-agent ${phaseState(1)}${selectedTaskId === task.id ? ' is-selected' : ''}${matches ? '' : ' is-filtered-out'}`}
                      aria-pressed={selectedTaskId === task.id}
                      disabled={!matches}
                      onClick={() => selectTask(task.id)}
                      style={{ '--task-tone': task.tone } as React.CSSProperties}
                    >
                      <span className="nx-orchestration-agent__identity"><img src={task.avatar} alt="" /><img src={task.logo} alt="" /><span><strong>{task.name}</strong><small>{task.role}</small></span></span>
                      <span className={`nx-orchestration-agent__status is-${statusClass(task.status)}`}><StatusIcon aria-hidden="true" />{task.status}</span>
                      <span className={`nx-orchestration-agent__runtime is-${task.runtimeKind}`}>{task.runtimeLabel}</span>
                    </button>
                  );
                })}
              </div>

              <button
                type="button"
                className={`nx-orchestration-node nx-orchestration-node--nexus ${phaseState(2)}${selectedTaskId === 'nexus-composition' ? ' is-selected' : ''}`}
                aria-pressed={selectedTaskId === 'nexus-composition'}
                onClick={() => selectTask('nexus-composition')}
                style={{ '--task-tone': '#f2a33a' } as React.CSSProperties}
              >
                <span className="nx-orchestration-node__icon"><GitBranch aria-hidden="true" /></span>
                <span><small>Deterministic compose</small><strong>NEXUS</strong><em>{data.contributedCount} inputs carried</em></span>
              </button>

              <button
                type="button"
                className={`nx-orchestration-node nx-orchestration-node--decision ${phaseState(3)}${selectedTaskId === 'human-decision' ? ' is-selected' : ''}`}
                aria-pressed={selectedTaskId === 'human-decision'}
                onClick={() => selectTask('human-decision')}
                style={{ '--task-tone': '#7da9ff' } as React.CSSProperties}
              >
                <span className="nx-orchestration-node__icon"><UserCheck aria-hidden="true" /></span>
                <span><small>Named human gate</small><strong>{data.operatorName}</strong><em>{data.recState}</em></span>
              </button>
            </div>
          </section>

          <section className="nx-orchestration__waterfall" aria-labelledby="nx-waterfall-title">
            <header><div><span>Execution waterfall</span><h2 id="nx-waterfall-title">Relative phase slots</h2></div><small>Sequence only · not elapsed time</small></header>
            <div className="nx-orchestration-waterfall__axis" aria-hidden="true">
              <span>Evidence frozen</span><span>Desks assessed</span><span>NEXUS compose</span><span>Human decision</span>
            </div>
            <div className="nx-orchestration-waterfall__rows">
              {inspectorTasks.map(task => {
                const deskTask = data.tasks.find(candidate => candidate.id === task.id);
                const isFiltered = Boolean(deskTask && !taskMatchesFilter(deskTask));
                const slot = { left: `${task.phase * 25 + 1}%`, width: '23%' };
                return (
                  <button
                    type="button"
                    key={task.id}
                    data-waterfall-task={task.id}
                    className={`${phaseState(task.phase)}${selectedTaskId === task.id ? ' is-selected' : ''}${isFiltered ? ' is-filtered-out' : ''}`}
                    aria-pressed={selectedTaskId === task.id}
                    disabled={isFiltered}
                    onClick={() => selectTask(task.id)}
                    style={{ '--task-tone': task.tone } as React.CSSProperties}
                  >
                    <span className="nx-orchestration-waterfall__label">{task.avatar ? <img src={task.avatar} alt="" /> : taskIcon(task.kind)}<strong>{task.name}</strong></span>
                    <span className="nx-orchestration-waterfall__track"><i style={slot}></i></span>
                    <small>{task.status}</small>
                  </button>
                );
              })}
            </div>
          </section>
        </main>

        <aside className="nx-orchestration__inspector" aria-live="polite" aria-label="Selected task inspector" style={{ '--task-tone': selectedTask.tone } as React.CSSProperties}>
          <header><span>Task inspector</span><small>current recorded state</small></header>
          <div className="nx-orchestration-inspector__identity">
            {selectedTask.avatar ? <img src={selectedTask.avatar} alt="" /> : <span>{taskIcon(selectedTask.kind)}</span>}
            {selectedTask.logo ? <img src={selectedTask.logo} alt="" /> : null}
            <div><small>{selectedTask.kind}</small><h2>{selectedTask.name}</h2><p>{selectedTask.role}</p></div>
          </div>
          <div className={`nx-orchestration-inspector__status is-${statusClass(selectedTask.status)}`}>
            <span>{selectedTask.status}</span><small>phase {selectedTask.phase + 1} / {PHASES.length}</small>
          </div>
          <dl className="nx-orchestration-inspector__facts">
            <div><dt>Execution mode</dt><dd>{selectedTask.runtime}</dd></div>
            <div><dt>Runtime record</dt><dd>{selectedTask.detail}</dd></div>
            <div><dt>Evidence</dt><dd>{selectedTask.evidence}</dd></div>
          </dl>
          <section><span>Recorded output</span><p>{selectedTask.finding}</p></section>
          <section><span>Operational note</span><p>{selectedTask.note}</p></section>
          <section className="nx-orchestration-inspector__boundary"><span>Authority boundary</span><p>{selectedTask.boundary}</p></section>
          <footer><AlertTriangle aria-hidden="true" /><span><strong>Prototype boundary</strong>No OMA run, trace, event stream, timing, or token data is connected.</span></footer>
        </aside>
      </div>
    </section>
  );
}
