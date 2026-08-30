import { Chip } from './Chip';
import { Figure } from './Figure';

export function SituationHero({
  priority,
  time,
  owner,
  situation,
  subtitle,
}: {
  priority: 'high' | 'medium' | 'low' | 'clear';
  time: string;
  owner: string;
  situation: string;
  subtitle: string;
}) {
  const chipTone = priority === 'high' ? 'danger' : priority === 'medium' ? 'accent' : priority === 'clear' ? 'ok' : 'muted';
  const chipLabel = priority === 'clear' ? 'Clear' : priority;
  return (
    <section className="wall-hero" aria-label="Situation">
      <div className="wall-hero__meta">
        <Chip tone={chipTone} label={chipLabel} />
        <span className="wall-eyebrow"><Figure>{time}</Figure></span>
        <span className="wall-eyebrow">{owner ? `Owner ${owner}` : 'Owner unassigned'}</span>
      </div>
      <h1 key={situation} className="wall-hero__situation">{situation}</h1>
      <p className="wall-hero__subtitle">{subtitle}</p>
    </section>
  );
}
