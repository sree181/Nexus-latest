import { useState } from 'react';
import { Figure } from './Figure';
import { useIdleTimer } from './IdleTimer';
import { WallButton } from './WallButton';

export interface SlideRow {
  id: string;
  title: string;
  status?: string;
}

export function SlideOver({
  title,
  rows,
  onClose,
  onSelect,
}: {
  title: string;
  rows: SlideRow[];
  onClose: () => void;
  onSelect?: (id: string) => void;
}) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(rows.length / 8));
  const visible = rows.slice(page * 8, page * 8 + 8);
  useIdleTimer(30_000, onClose);

  return (
    <div className="wall-slide" role="presentation" onPointerDown={event => { if (event.currentTarget === event.target) onClose(); }}>
      <div />
      <aside className="wall-slide__panel" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {visible.map(row => (
          <button
            key={row.id}
            type="button"
            className="wall-slide__row"
            onPointerDown={() => onSelect?.(row.id)}
          >
            <strong>{row.title}</strong>
            {row.status && <small>{row.status}</small>}
          </button>
        ))}
        <p className="wall-page"><Figure>{page + 1}</Figure> of <Figure>{pages}</Figure></p>
        <div className="wall-actions">
          {page > 0 && <WallButton tone="secondary" label="Previous" onPress={() => setPage(current => current - 1)} />}
          {page + 1 < pages && <WallButton tone="secondary" label="Next" onPress={() => setPage(current => current + 1)} />}
          <WallButton tone="text" label="Close" onPress={onClose} />
        </div>
      </aside>
    </div>
  );
}

export function AssignDialog({
  title,
  operators,
  onAssign,
  onClose,
}: {
  title: string;
  operators: Array<{ id: string; name: string; role: string }>;
  onAssign: (id: string) => void;
  onClose: () => void;
}) {
  useIdleTimer(30_000, onClose);
  return (
    <div className="wall-slide wall-slide--modal" role="presentation" onPointerDown={event => { if (event.currentTarget === event.target) onClose(); }}>
      <section className="wall-assign" role="dialog" aria-modal="true" aria-labelledby="wall-assign-title">
        <h2 id="wall-assign-title">{title}</h2>
        {operators.length === 0 && <p className="wall-empty">No named operators are available on this wall.</p>}
        {operators.map(operator => (
          <button
            key={operator.id}
            type="button"
            className="wall-slide__row"
            onPointerDown={() => onAssign(operator.id)}
          >
            <strong>{operator.name}</strong>
            <small>{operator.role}</small>
          </button>
        ))}
        <div className="wall-actions">
          <WallButton tone="primary" label="Assign" onPress={() => operators[0] && onAssign(operators[0].id)} />
          <WallButton tone="text" label="Cancel" onPress={onClose} />
        </div>
      </section>
    </div>
  );
}
