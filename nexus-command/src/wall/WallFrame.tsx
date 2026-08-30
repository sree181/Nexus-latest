import type { ReactNode } from 'react';

export function WallFrame({
  top,
  hero,
  body,
  status,
}: {
  top: ReactNode;
  hero: ReactNode;
  body: ReactNode;
  status: ReactNode;
}) {
  return (
    <div className="wall-frame" id="app">
      {top}
      {hero}
      {body}
      {status}
    </div>
  );
}
