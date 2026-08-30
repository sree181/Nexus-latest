import type { ImpactRow, WallDesk } from '../lib/wallSelectors';
import { AgentsPanel } from './AgentsPanel';
import { ImpactPanel } from './ImpactPanel';
import { IncidentMap } from './IncidentMap';

export function OperationsDesk({
  center,
  markerLabel,
  impact,
  desks,
  onAssign,
}: {
  center: [number, number] | null;
  markerLabel: string;
  impact: ImpactRow[];
  desks: WallDesk[];
  onAssign: (desk: WallDesk) => void;
}) {
  return (
    <div className="wall-body">
      <IncidentMap center={center} markerLabel={markerLabel} />
      <div className="wall-stack">
        <ImpactPanel rows={impact} />
        <AgentsPanel desks={desks} onAssign={onAssign} />
      </div>
    </div>
  );
}
