import type { WallScreen } from '../lib/wallSelectors';
import { NavCluster } from './NavCluster';
import { WallButton } from './WallButton';

export interface RailAction {
  tone: 'primary' | 'secondary' | 'text' | 'clear';
  label: string;
  onPress: () => void;
}

export function StatusStrip({
  active,
  actions,
  onSelect,
}: {
  active: WallScreen;
  actions: RailAction[];
  onSelect: (screen: WallScreen) => void;
}) {
  return (
    <footer className="wall-status">
      <NavCluster active={active} onSelect={onSelect} />
      <div className="wall-rail-actions">
        {actions.map(action => (
          <WallButton key={action.label} tone={action.tone} label={action.label} onPress={action.onPress} />
        ))}
      </div>
      <NavCluster active={active} onSelect={onSelect} />
    </footer>
  );
}
