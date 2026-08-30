import { useEffect, useMemo, useState } from 'react';
import type { LineageStage } from '../lib/wallSelectors';
import { walkPath } from '../lib/wallSelectors';
import { useIdleTimer } from './IdleTimer';
import { LineageColumn } from './LineageColumn';
import { PathOverlay } from './PathOverlay';
import { SlideOver } from './SlideOver';

export function DecisionLineage({ stages }: { stages: LineageStage[] }) {
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [expanded, setExpanded] = useState<LineageStage | null>(null);

  const decisionId = stages.find(stage => stage.key === 'decision')?.allItems[0]?.id ?? null;
  const cycleIds = useMemo(() => {
    const decision = stages.find(stage => stage.key === 'decision')?.allItems ?? [];
    const rest = stages.flatMap(stage => stage.key === 'decision' ? [] : stage.items);
    return [...decision, ...rest].map(item => item.id);
  }, [stages]);

  useIdleTimer(30_000, () => {
    setTouched(false);
    setFocusedId(null);
    setExpanded(null);
  }, touched || Boolean(expanded));

  useEffect(() => {
    if (touched || expanded || cycleIds.length === 0) return undefined;
    let index = 0;
    setFocusedId(cycleIds[0] ?? decisionId);
    const timer = window.setInterval(() => {
      index = (index + 1) % cycleIds.length;
      setFocusedId(cycleIds[index] ?? null);
    }, 12_000);
    return () => window.clearInterval(timer);
  }, [cycleIds, decisionId, expanded, touched]);

  const pathIds = focusedId ? walkPath(focusedId, stages) : null;

  return (
    <div className="wall-lineage-host">
      <div
        ref={setHost}
        className="wall-lineage"
        onPointerDown={event => {
          if (event.target === event.currentTarget) {
            setTouched(true);
            setFocusedId(null);
          }
        }}
      >
        {stages.map(stage => (
          <LineageColumn
            key={stage.key}
            stage={stage}
            focused={pathIds}
            onPress={id => {
              setTouched(true);
              setFocusedId(id);
            }}
            onMore={next => {
              setTouched(true);
              setExpanded(next);
            }}
          />
        ))}
        <PathOverlay focusedId={focusedId} pathIds={pathIds} stages={stages} host={host} />
      </div>
      {expanded && (
        <SlideOver
          title={expanded.label}
          rows={expanded.allItems}
          onClose={() => setExpanded(null)}
          onSelect={id => {
            setFocusedId(id);
            setExpanded(null);
            setTouched(true);
          }}
        />
      )}
    </div>
  );
}
