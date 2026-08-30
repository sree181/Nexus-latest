import { useEffect, useState } from 'react';

export function FunnelBand({
  stages,
}: {
  stages: Array<{ key: string; label: string; count: number }>;
}) {
  const [shown, setShown] = useState(stages.map(() => 0));
  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      setShown(stages.map(stage => stage.count));
      return undefined;
    }
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 600);
      setShown(stages.map(stage => Math.round(stage.count * t)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [stages.map(stage => stage.count).join(',')]);

  const max = Math.max(1, ...stages.map(stage => stage.count));
  const width = 3648;
  const height = 200;
  const step = width / stages.length;
  const ribbons = stages.slice(0, -1).map((stage, index) => {
    const next = stages[index + 1];
    const band = Math.max(24, Math.min(200, (Math.min(stage.count, next.count) / max) * 200));
    const x1 = step * index + step / 2;
    const x2 = step * (index + 1) + step / 2;
    const y1 = (height - band) / 2;
    const accent = index === 3 || index === 4;
    return { x1, x2, y1, band, accent };
  });

  return (
    <section className="wall-funnel" aria-label="Decision lineage">
      <p className="wall-eyebrow wall-funnel__title">Decision Lineage</p>
      <div className="wall-funnel__row">
        {stages.map((stage, index) => (
          <div key={stage.key} className="wall-funnel__stage">
            <p className={`wall-funnel__count${stage.key === 'decision' ? ' is-decision' : ''}`}>{shown[index] ?? stage.count}</p>
            <p className="wall-funnel__label">{stage.label}</p>
          </div>
        ))}
      </div>
      <svg className="wall-funnel__ribbon" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        {ribbons.map((ribbon, index) => {
          const mid = (ribbon.x1 + ribbon.x2) / 2;
          const top = `M ${ribbon.x1} ${ribbon.y1} C ${mid} ${ribbon.y1}, ${mid} ${ribbon.y1}, ${ribbon.x2} ${ribbon.y1}`;
          const bottom = `L ${ribbon.x2} ${ribbon.y1 + ribbon.band} C ${mid} ${ribbon.y1 + ribbon.band}, ${mid} ${ribbon.y1 + ribbon.band}, ${ribbon.x1} ${ribbon.y1 + ribbon.band} Z`;
          return (
            <path
              key={index}
              d={`${top} ${bottom}`}
              fill={ribbon.accent ? 'var(--accent)' : 'var(--text-3)'}
              opacity={ribbon.accent ? 0.85 : 0.6}
            />
          );
        })}
      </svg>
    </section>
  );
}
