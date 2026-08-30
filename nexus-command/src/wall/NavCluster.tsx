import type { WallScreen } from '../lib/wallSelectors';

const SCREENS: Array<{ id: WallScreen; label: string; icon: string }> = [
  { id: 'operations', label: 'Operations', icon: 'M6 6h20v20H6zM38 6h20v20H38zM6 38h20v20H6zM38 38h20v20H38z' },
  { id: 'lineage', label: 'Lineage', icon: 'M8 32h16v8H8zM40 12h16v8H40zM40 44h16v8H40zM24 36h16M48 20v24' },
  { id: 'coordination', label: 'Coordination', icon: 'M32 14a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM16 50c0-8 8-12 16-12s16 4 16 12' },
  { id: 'mobility', label: 'Mobility Flow', icon: 'M8 32h40M40 20l16 12-16 12' },
];

export function NavCluster({
  active,
  onSelect,
}: {
  active: WallScreen;
  onSelect: (screen: WallScreen) => void;
}) {
  return (
    <nav className="wall-nav" aria-label="Wall screens">
      {SCREENS.map(screen => (
        <button
          key={screen.id}
          type="button"
          className={active === screen.id ? 'is-active' : undefined}
          onPointerDown={() => onSelect(screen.id)}
        >
          <svg viewBox="0 0 64 64" aria-hidden="true">
            <path d={screen.icon} fill="none" stroke="currentColor" strokeWidth="4" />
          </svg>
          {screen.label}
        </button>
      ))}
    </nav>
  );
}
