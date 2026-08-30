import { useState } from 'react';
import type { OperationalEvent, ScenarioPack } from '../operationalTypes';
import { phaseLabel } from '../uiCopy';

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
        <span>{pack.agentCodes.length} desks</span>
        <span>{pack.ruleCount} rules</span>
        <span>Starts as {phaseLabel(pack.defaultPhase)}</span>
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
            <h2 id="operating-window-title">Start coordinating</h2>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close">Close</button>
        </div>

        <div className="dialog-summary">
          <span>What this chooses</span>
          <p>
            The scenario picks which official feeds we read, which desks review them, and which rules can open
            an incident. Nexus records decisions. It never controls a signal, radio, or field system.
          </p>
        </div>

        <div className="pack-grid">
          {packs.length === 0 && <p className="pack-grid__empty">No scenarios are available yet.</p>}
          {packs.map(pack => (
            <PackCard key={pack.packCode} pack={pack} selected={pack.packCode === packCode} onSelect={() => setPackCode(pack.packCode)} />
          ))}
        </div>

        <label className="field-label">
          What to call this window
          <input type="text" value={name} placeholder={resolvedName} onChange={event => setName(event.target.value)} disabled={busy} />
        </label>

        <label className="field-label">
          Area
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
            {busy ? 'Opening…' : 'Start coordinating'}
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
        <span className="system-code">Idle</span>
        <h1>Nothing is being coordinated right now</h1>
        <p>
          Official feeds are read and rules can open incidents only while a window is open.
          {canManage ? ' Choose a scenario to begin.' : ' Starting a window needs a command-lead account.'}
        </p>
        <div className="system-panel__actions">
          <button className="button button--secondary" type="button" onClick={onRetry}>Check again</button>
          {canManage && <button className="button button--approve" type="button" onClick={onOpenWindow}>Start coordinating</button>}
        </div>
      </div>
    </main>
  );
}

export function OperatingWindowChip({
  canManage, onClose, onChange,
}: {
  event: OperationalEvent;
  pack: ScenarioPack | null;
  canManage: boolean;
  onClose: () => void;
  onChange: () => void;
}) {
  return (
    <div className="header-field header-field--scenario" aria-label="Active window">
      {canManage && (
        <div className="scenario-actions">
          <button type="button" onClick={onChange}>Switch window</button>
          <button type="button" onClick={onClose}>Close</button>
        </div>
      )}
    </div>
  );
}
