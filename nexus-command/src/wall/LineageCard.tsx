import type { LineageItem } from '../lib/wallSelectors';
import { Figure } from './Figure';

export function LineageCard({
  item,
  compact,
  decision,
  focused,
  dimmed,
  onPress,
}: {
  item: LineageItem;
  compact?: boolean;
  decision?: boolean;
  focused?: boolean;
  dimmed?: boolean;
  onPress: (id: string) => void;
}) {
  const classes = [
    'wall-card',
    compact ? 'is-compact' : '',
    decision ? 'is-decision' : '',
    focused ? 'is-focus' : '',
    dimmed ? 'is-dim' : '',
  ].filter(Boolean).join(' ');
  return (
    <button
      type="button"
      className={classes}
      data-node-id={item.id}
      onPointerDown={() => onPress(item.id)}
    >
      <strong>{item.title}</strong>
      <small>
        {item.count && item.count > 1 ? <>x<Figure>{item.count}</Figure></> : item.status}
      </small>
    </button>
  );
}
