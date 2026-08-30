import type { ImpactRow } from '../lib/wallSelectors';
import { Figure } from './Figure';

export function ImpactPanel({ rows }: { rows: ImpactRow[] }) {
  return (
    <section className="wall-impact" aria-label="What is affected">
      {rows.slice(0, 3).map(row => (
        <div
          key={row.category}
          className={`wall-impact__cell${row.figure === 0 ? ' is-zero' : ''}${row.named ? ' is-named' : ''}`}
        >
          <span className="wall-eyebrow">{row.category}</span>
          <span className="wall-impact__value">
            <Figure>{row.figure}</Figure> {row.unit}
          </span>
        </div>
      ))}
    </section>
  );
}
