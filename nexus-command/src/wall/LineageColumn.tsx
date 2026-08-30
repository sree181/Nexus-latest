import type { LineageItem, LineageStage } from '../lib/wallSelectors';
import { Figure } from './Figure';
import { LineageCard } from './LineageCard';

export function LineageColumn({
  stage,
  focused,
  onPress,
  onMore,
}: {
  stage: LineageStage;
  focused: Set<string> | null;
  onPress: (id: string) => void;
  onMore: (stage: LineageStage) => void;
}) {
  const emptyVerified = stage.key === 'verification' && stage.allItems.length === 0;
  return (
    <section className="wall-column" data-stage={stage.key}>
      <div className="wall-column__head">
        {stage.label}
        <span><Figure>{stage.count}</Figure></span>
      </div>
      {emptyVerified && (
        <div className="wall-empty">
          <p>No field verification yet</p>
          <small className="wall-eyebrow">Waiting on: Field Operations</small>
        </div>
      )}
      {stage.items.map(item => (
        <StageCard
          key={item.id}
          stage={stage.key}
          item={item}
          focused={focused}
          onPress={onPress}
        />
      ))}
      {stage.more > 0 && (
        <button type="button" className="wall-more" onPointerDown={() => onMore(stage)}>
          +<Figure>{stage.more}</Figure> more
        </button>
      )}
    </section>
  );
}

function StageCard({
  stage,
  item,
  focused,
  onPress,
}: {
  stage: string;
  item: LineageItem;
  focused: Set<string> | null;
  onPress: (id: string) => void;
}) {
  return (
    <LineageCard
      item={item}
      compact={stage === 'finding'}
      decision={stage === 'decision'}
      focused={Boolean(focused?.has(item.id))}
      dimmed={Boolean(focused && !focused.has(item.id))}
      onPress={onPress}
    />
  );
}
