import { useState } from 'react';
import type { OperationalEvent, ScenarioPack } from '../operationalTypes';

function titleCase(value: string): string {
  return value.replace(/[_-]/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function PackCard({ pack, selected, onSelect }: { pack: ScenarioPack; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className={`pack-card${selected ? ' pack-card--selected' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <h3>{pack.name}</h3>
      <p>{pack.description}</p>
      <div className="pack-card__facts">
        <span>{pack.connectorCodes.length} feeds</span>
        <span>{pack.agentCodes.length} agent desks</span>
        <span>{pack.ruleCount} detection rules</span>
        <span>Opens in {titleCase(pack.defaultPhase)}</span>
      </div>
    </button>
  );
}

export function OperatingWindowDialog({
  packs, busy, error, onClose, onOpen,
}: {
  packs: ScenarioPack[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onOpen: (input: { packCode: string; name: string; locationName: string }) => void;
}) {
  const [packCode, setPackCode] = useState(packs[0]?.packCode ?? '');
  const [name, setName] = useState('');
  const [locationName, setLocationName] = useState('Auburn, Alabama');
  const selected = packs.find(pack => pack.packCode === packCode) ?? null;
  const resolvedName = name.trim() || (selected ? `${selected.name} — ${new Date().toLocaleDateString()}` : '');

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={event => { if (event.currentTarget === event.target && !busy) onClose(); }}>
      <section className="decision-dialog" role="dialog" aria-modal="true" aria-labelledby="operating-window-title">
        <div className="decision-dialog__header">
          <div>
            <span>Operating window</span>
            <h2 id="operating-window-title">Open a scenario</h2>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close">Close</button>
        </div>

        <div className="dialog-summary">
          <span>What a scenario changes</span>
          <p>
            The scenario decides which authoritative feeds are read, which agent desks are staffed, and which
            detection rules may open an incident. Nexus coordinates and records decisions in every scenario; it
            never controls an agency system.
          </p>
        </div>

        <div className="pack-grid">
          {packs.length === 0 && <p className="pack-grid__empty">No scenario pack is available from the operational database.</p>}
          {packs.map(pack => (
            <PackCard key={pack.packCode} pack={pack} selected={pack.packCode === packCode} onSelect={() => setPackCode(pack.packCode)} />
          ))}
        </div>

        <label className="field-label">
          Window name
          <input type="text" value={name} placeholder={resolvedName} onChange={event => setName(event.target.value)} disabled={busy} />
        </label>

        <label className="field-label">
          Operating area
          <input type="text" value={locationName} onChange={event => setLocationName(event.target.value)} disabled={busy} />
        </label>

        {error && <div className="form-error">{error}</div>}

        <div className="decision-dialog__actions">
          <button className="button button--secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            className="button button--approve"
            type="button"
            disabled={busy || !selected || resolvedName.length < 3 || locationName.trim().length < 2}
            onClick={() => onOpen({ packCode, name: resolvedName, locationName: locationName.trim() })}
          >
            {busy ? 'Opening…' : 'Open operating window'}
          </button>
        </div>
      </section>
    </div>
  );
}

export function NoOperatingWindowScreen({
  canManage, onOpenWindow, onRetry,
}: {
  canManage: boolean;
  onOpenWindow: () => void;
  onRetry: () => void;
}) {
  return (
    <main className="system-screen">
      <div className="system-panel">
        <span className="system-code">NO OPERATING WINDOW</span>
        <h1>Nothing is being coordinated right now</h1>
        <p>
          Nexus reads authoritative feeds and evaluates detection rules only inside an open operating window.
          {canManage ? ' Open a scenario to begin.' : ' Opening a window requires the command-lead role.'}
        </p>
        <div className="system-panel__actions">
          <button className="button button--secondary" type="button" onClick={onRetry}>Check again</button>
          {canManage && <button className="button button--approve" type="button" onClick={onOpenWindow}>Open a scenario</button>}
        </div>
      </div>
    </main>
  );
}

export function OperatingWindowChip({
  event, pack, canManage, onClose, onChange,
}: {
  event: OperationalEvent;
  pack: ScenarioPack | null;
  canManage: boolean;
  onClose: () => void;
  onChange: () => void;
}) {
  return (
    <div className="header-field header-field--scenario" aria-label="Active scenario">
      <span>Scenario</span>
      <strong>{pack?.name ?? titleCase(event.eventType)}</strong>
      {canManage && (
        <div className="scenario-actions">
          <button type="button" onClick={onChange}>Switch</button>
          <button type="button" onClick={onClose}>Close</button>
        </div>
      )}
    </div>
  );
}
