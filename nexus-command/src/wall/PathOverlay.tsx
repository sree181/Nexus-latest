import { useLayoutEffect, useState } from 'react';
import type { LineageStage } from '../lib/wallSelectors';

interface Path {
  d: string;
}

export function PathOverlay({
  focusedId,
  pathIds,
  stages,
  host,
}: {
  focusedId: string | null;
  pathIds: Set<string> | null;
  stages: LineageStage[];
  host: HTMLElement | null;
}) {
  const [paths, setPaths] = useState<Path[]>([]);

  useLayoutEffect(() => {
    if (!focusedId || !pathIds || !host) {
      setPaths([]);
      return;
    }
    const items = stages.flatMap(stage => stage.allItems);
    const byId = new Map(items.map(item => [item.id, item]));
    const origin = host.getBoundingClientRect();
    const box = (id: string) => {
      const el = host.querySelector(`[data-node-id="${id}"]`);
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return {
        left: rect.left - origin.left,
        right: rect.right - origin.left,
        mid: rect.top - origin.top + rect.height / 2,
      };
    };
    const next: Path[] = [];
    for (const id of pathIds) {
      const item = byId.get(id);
      if (!item) continue;
      const from = box(id);
      if (!from) continue;
      for (const child of item.children) {
        if (!pathIds.has(child)) continue;
        const to = box(child);
        if (!to) continue;
        const mid = (from.right + to.left) / 2;
        next.push({
          d: `M ${from.right} ${from.mid} C ${mid} ${from.mid}, ${mid} ${to.mid}, ${to.left} ${to.mid}`,
        });
      }
    }
    setPaths(next);
  }, [focusedId, host, pathIds, stages]);

  if (!paths.length) return null;
  return (
    <svg className="wall-path" aria-hidden="true">
      {paths.map(path => <path key={path.d} d={path.d} />)}
    </svg>
  );
}
