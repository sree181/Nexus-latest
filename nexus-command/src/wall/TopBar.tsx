import { useEffect, useState } from 'react';
import type { WallFeed } from '../lib/wallSelectors';
import { Figure } from './Figure';

export function TopBar({
  windowName,
  feedsLive,
  feedsTotal,
  feeds,
  clock,
  live,
}: {
  windowName: string;
  feedsLive: number;
  feedsTotal: number;
  feeds: WallFeed[];
  clock: string;
  live: boolean;
}) {
  const stale = feeds.find(feed => feed.tone !== 'ok') ?? null;
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    if (!stale) {
      setFlash(null);
      return undefined;
    }
    const ageMin = stale.lastSeenAt
      ? Math.max(1, Math.round((Date.now() - Date.parse(stale.lastSeenAt)) / 60_000))
      : 1;
    setFlash(`${stale.name} stale ${ageMin} min`);
    const timer = window.setTimeout(() => setFlash(null), 10_000);
    return () => window.clearTimeout(timer);
  }, [stale?.name, stale?.lastSeenAt]);

  return (
    <header className="wall-top">
      <div>
        <strong className="wall-wordmark">NEXUS COORDINATE</strong>
        <span className="wall-window">{windowName}</span>
      </div>
      <div className="wall-top__right">
        <span className={`wall-live${live ? '' : ' wall-live--practice'}`}>
          <i className="wall-live__dot" aria-hidden="true" />
          {live ? 'Live' : 'Practice'}
        </span>
        <span className={`wall-feeds${stale ? ' is-stale' : ''}`}>
          {flash ?? <><Figure>{feedsLive}</Figure> of <Figure>{feedsTotal}</Figure> feeds</>}
        </span>
        <time className="wall-clock" dateTime={clock}><Figure>{clock}</Figure></time>
      </div>
    </header>
  );
}
