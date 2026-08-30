import type { ReactNode } from 'react';

export function Figure({ children }: { children: ReactNode }) {
  return <span className="wall-figure">{children}</span>;
}
