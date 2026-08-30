import type { WallDesk } from '../lib/wallSelectors';
import { Figure } from './Figure';
import { ReachGuard } from './ReachGuard';

function stamp(value: string | null): string | null {
  if (!value) return null;
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function AgentsPanel({
  desks,
  onAssign,
}: {
  desks: WallDesk[];
  onAssign: (desk: WallDesk) => void;
}) {
  const staffed = desks.filter(desk => desk.staffed).length;
  const tone = staffed === 0 ? 'is-none' : staffed === desks.length ? 'is-full' : 'is-partial';
  return (
    <section className="wall-panel wall-agents" aria-label="Agents">
      <div className="wall-agents__title">
        <h2>Agents</h2>
        <p className={`wall-agents__count ${tone}`}>
          <Figure>{staffed}</Figure> of <Figure>{desks.length}</Figure> staffed
        </p>
      </div>
      <ReachGuard>
        <div className="wall-agents__grid">
          {desks.map(desk => {
            const time = stamp(desk.lastAt);
            return (
              <button
                key={desk.code}
                type="button"
                className={desk.staffed ? 'agent-tile' : 'agent-tile is-empty'}
                data-agent-tile="true"
                onPointerDown={() => onAssign(desk)}
              >
                <img src={desk.icon} alt="" />
                <span className="agent-tile__name">{desk.name}</span>
                <span className="agent-tile__role">{desk.role}</span>
                <span className="agent-tile__state">
                  {desk.staffed ? <><i aria-hidden="true" />{desk.operator || 'Staffed'}</> : 'Unstaffed'}
                </span>
                <span className={desk.lastTitle ? 'agent-tile__last' : 'agent-tile__last is-idle'}>
                  {desk.lastTitle && time
                    ? <>{desk.lastTitle} · <Figure>{time}</Figure></>
                    : 'No activity this window'}
                </span>
              </button>
            );
          })}
        </div>
      </ReachGuard>
    </section>
  );
}
