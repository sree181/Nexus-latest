import type { WallDesk } from '../lib/wallSelectors';

export function StaffingPanel({
  desks,
  onAssign,
}: {
  desks: WallDesk[];
  onAssign: (desk: WallDesk) => void;
}) {
  const staffed = desks.filter(desk => desk.staffed).length;
  const tone = staffed === 0 ? 'is-none' : staffed === desks.length ? 'is-full' : 'is-partial';
  return (
    <section className="wall-panel" aria-label="Desk staffing">
      <div className="wall-staffing__title">
        <h2>Desk staffing</h2>
        <strong className={`wall-staffing__count wall-data ${tone}`}>{staffed} of {desks.length} staffed</strong>
      </div>
      <div className="wall-desks">
        {desks.map(desk => (
          <button
            key={desk.code}
            type="button"
            className={desk.staffed ? 'wall-desk' : 'wall-desk is-empty'}
            onPointerDown={() => onAssign(desk)}
          >
            <img src={desk.icon} alt="" />
            <span>
              <strong>{desk.name}</strong>
              <small>{desk.role}</small>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
