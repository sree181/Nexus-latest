import type { ReactNode } from 'react';

export function ReachGuard({
  children,
  minY = 1080,
}: {
  children: ReactNode;
  minY?: number;
}) {
  return <div className="wall-reach" data-reach-min={minY}>{children}</div>;
}
