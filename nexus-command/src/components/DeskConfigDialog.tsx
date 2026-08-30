import { useEffect, useState } from 'react';
import { operationalApi, OperationalApiError } from '../operationalApi';
import type { AtlasAgentProfile, AtlasPolicy } from '../operationalTypes';

type ConfigurableDesk = 'atlas' | 'aqua';
type Tab = 'identity' | 'instructions' | 'model' | 'tools' | 'policies';

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'identity', label: 'Role' },
  { id: 'instructions', label: 'Prompt' },
  { id: 'model', label: 'Model' },
  { id: 'tools', label: 'Tools' },
  { id: 'policies', label: 'Policies' },
];

const DESK_COPY: Record<ConfigurableDesk, {
  kicker: string;
  title: string;
  name: string;
  summary: string;
  loading: string;
  loadError: string;
  saveError: string;
  saveLabel: string;
  policiesHint: string;
  sourcePlaceholder: string;
}> = {
  atlas: {
    kicker: 'Traffic desk',
    title: 'Configure ATLAS',
    name: 'ATLAS',
    summary: 'Role, backstory, prompt, model, tools, and policy notes. ATLAS still cannot change a signal, close a road, or publish a message. The API key stays on the server.',
    loading: 'Loading ATLAS…',
    loadError: 'Could not load ATLAS.',
    saveError: 'ATLAS could not be saved.',
    saveLabel: 'Save ATLAS',
    policiesHint: 'Paste department, city, county, or state notes ATLAS may search. These are reference, not evidence, and do not open incidents.',
    sourcePlaceholder: 'Traffic Engineering memo, ALDOT page…',
  },
  aqua: {
    kicker: 'Parking and transit desk',
    title: 'Configure AQUA',
    name: 'AQUA',
    summary: 'Role, backstory, prompt, model, tools, and policy notes. AQUA still cannot change a schedule or parking policy. The API key stays on the server.',
    loading: 'Loading AQUA…',
    loadError: 'Could not load AQUA.',
    saveError: 'AQUA could not be saved.',
    saveLabel: 'Save AQUA',
    policiesHint: 'Paste department, city, county, or state notes AQUA may search. These are reference, not evidence, and do not open incidents.',
    sourcePlaceholder: 'Parking & Transit memo, ADA note…',
  },
};

function policyIdFromTitle(title: string, existing: string[]): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'note';
  let id = `policy:${slug}`;
  let n = 2;
  while (existing.includes(id)) {
    id = `policy:${slug}-${n}`;
    n += 1;
  }
  return id;
}

export function DeskConfigDialog({
  deskCode, busy, error, onClose, onSaved,
}: {
  deskCode: ConfigurableDesk;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const copy = DESK_COPY[deskCode];
  const [tab, setTab] = useState<Tab>('identity');
  const [profile, setProfile] = useState<AtlasAgentProfile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    role: '',
    backstory: '',
    instructions: '',
    model: '',
    customModel: '',
    temperature: 0.1,
    maxTurns: 8,
    timeoutMs: 20_000,
    enabledTools: [] as string[],
    policies: [] as AtlasPolicy[],
  });
  const [policyForm, setPolicyForm] = useState({
    title: '',
    jurisdiction: 'department' as AtlasPolicy['jurisdiction'],
    source: '',
    body: '',
  });

  useEffect(() => {
    let cancelled = false;
    operationalApi.deskProfile(deskCode).then(next => {
      if (cancelled) return;
      setProfile(next);
      const known = next.runtime.models.some(model => model.id === next.llm.model);
      setDraft({
        role: next.role,
        backstory: next.backstory,
        instructions: next.instructions,
        model: known ? next.llm.model : 'custom',
        customModel: known ? '' : next.llm.model,
        temperature: next.llm.temperature,
        maxTurns: next.llm.maxTurns,
        timeoutMs: next.llm.timeoutMs,
        enabledTools: next.tools.filter(tool => tool.enabled).map(tool => tool.name),
        policies: next.policies,
      });
    }).catch(reason => {
      if (!cancelled) {
        setLoadError(reason instanceof OperationalApiError ? reason.message : copy.loadError);
      }
    });
    return () => { cancelled = true; };
  }, [copy.loadError, deskCode]);

  const save = async () => {
    if (!profile) return;
    setSaving(true);
    setSaveError(null);
    try {
      await operationalApi.saveDeskProfile(deskCode, {
        role: draft.role,
        backstory: draft.backstory,
        instructions: draft.instructions,
        llm: {
          model: draft.model === 'custom' ? draft.customModel.trim() : draft.model,
          temperature: draft.temperature,
          maxTurns: draft.maxTurns,
          timeoutMs: draft.timeoutMs,
        },
        enabledTools: draft.enabledTools,
        policies: draft.policies,
      });
      await onSaved();
    } catch (reason) {
      setSaveError(reason instanceof OperationalApiError ? reason.message : copy.saveError);
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const next = await operationalApi.resetDeskProfile(deskCode);
      setProfile(next);
      setDraft({
        role: next.role,
        backstory: next.backstory,
        instructions: next.instructions,
        model: next.llm.model,
        customModel: '',
        temperature: next.llm.temperature,
        maxTurns: next.llm.maxTurns,
        timeoutMs: next.llm.timeoutMs,
        enabledTools: next.tools.filter(tool => tool.enabled).map(tool => tool.name),
        policies: next.policies,
      });
      await onSaved();
    } catch (reason) {
      setSaveError(reason instanceof OperationalApiError ? reason.message : 'Defaults could not be restored.');
    } finally {
      setSaving(false);
    }
  };

  const addPolicy = () => {
    if (policyForm.title.trim().length < 3 || policyForm.body.trim().length < 20) return;
    const id = policyIdFromTitle(policyForm.title, draft.policies.map(item => item.id));
    setDraft(current => ({
      ...current,
      policies: [...current.policies, {
        id,
        title: policyForm.title.trim(),
        jurisdiction: policyForm.jurisdiction,
        source: policyForm.source.trim() || 'Operator-supplied policy note',
        body: policyForm.body.trim(),
      }],
    }));
    setPolicyForm({ title: '', jurisdiction: 'department', source: '', body: '' });
  };

  const locked = busy || saving;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={event => { if (event.currentTarget === event.target && !locked) onClose(); }}>
      <section className="decision-dialog agent-dialog" role="dialog" aria-modal="true" aria-labelledby={`${deskCode}-config-title`}>
        <div className="decision-dialog__header">
          <div>
            <span>{copy.kicker}</span>
            <h2 id={`${deskCode}-config-title`}>{copy.title}</h2>
          </div>
          <button type="button" onClick={onClose} disabled={locked} aria-label="Close">Close</button>
        </div>

        <div className="dialog-summary">
          <span>What you can change</span>
          <p>{copy.summary}</p>
        </div>

        {profile && (
          <p className="agent-dialog__lock">{profile.locked.boundary}</p>
        )}

        <div className="agent-tabs" role="tablist">
          {TABS.map(item => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={tab === item.id ? 'is-active' : ''}
              onClick={() => setTab(item.id)}
              disabled={locked}
            >
              {item.label}
            </button>
          ))}
        </div>

        {!profile && !loadError && <p className="agent-dialog__hint">{copy.loading}</p>}
        {loadError && <div className="form-error">{loadError}</div>}

        {profile && tab === 'identity' && (
          <div className="agent-panel">
            <label className="field-label">
              Role
              <textarea rows={3} value={draft.role} disabled={locked} onChange={event => setDraft(current => ({ ...current, role: event.target.value }))} />
            </label>
            <label className="field-label">
              Backstory
              <textarea rows={7} value={draft.backstory} disabled={locked} onChange={event => setDraft(current => ({ ...current, backstory: event.target.value }))} />
            </label>
          </div>
        )}

        {profile && tab === 'instructions' && (
          <div className="agent-panel">
            <label className="field-label">
              Prompt
              <textarea rows={10} value={draft.instructions} disabled={locked} onChange={event => setDraft(current => ({ ...current, instructions: event.target.value }))} />
            </label>
            <p className="agent-dialog__hint">Locked rules about feeds and field control are always appended after this prompt.</p>
          </div>
        )}

        {profile && tab === 'model' && (
          <div className="agent-panel">
            <label className="field-label">
              Model
              <select
                value={draft.model}
                disabled={locked}
                onChange={event => setDraft(current => ({ ...current, model: event.target.value }))}
              >
                {profile.runtime.models.map(model => (
                  <option key={model.id} value={model.id}>{model.label}</option>
                ))}
                <option value="custom">Custom model id</option>
              </select>
            </label>
            {draft.model === 'custom' && (
              <label className="field-label">
                Custom model id
                <input value={draft.customModel} disabled={locked} onChange={event => setDraft(current => ({ ...current, customModel: event.target.value }))} />
              </label>
            )}
            <div className="agent-grid">
              <label className="field-label">
                Temperature
                <input type="number" min={0} max={1} step={0.05} value={draft.temperature} disabled={locked} onChange={event => setDraft(current => ({ ...current, temperature: Number(event.target.value) }))} />
              </label>
              <label className="field-label">
                Max turns
                <input type="number" min={2} max={12} value={draft.maxTurns} disabled={locked} onChange={event => setDraft(current => ({ ...current, maxTurns: Number(event.target.value) }))} />
              </label>
              <label className="field-label">
                Timeout (ms)
                <input type="number" min={3000} max={45000} step={1000} value={draft.timeoutMs} disabled={locked} onChange={event => setDraft(current => ({ ...current, timeoutMs: Number(event.target.value) }))} />
              </label>
            </div>
            <p className="agent-dialog__hint">
              Host {profile.runtime.host}. Key {profile.runtime.keyConfigured ? 'is set' : 'is missing'}.
              Agent loop {profile.runtime.enabled ? 'is on' : 'is off until ATLAS_AI_ENABLED and a key are set'}.
            </p>
          </div>
        )}

        {profile && tab === 'tools' && (
          <div className="agent-panel">
            {profile.tools.map(tool => (
              <label key={tool.name} className="agent-tool">
                <input
                  type="checkbox"
                  checked={draft.enabledTools.includes(tool.name)}
                  disabled={locked || tool.required}
                  onChange={event => {
                    setDraft(current => ({
                      ...current,
                      enabledTools: event.target.checked
                        ? [...current.enabledTools, tool.name]
                        : current.enabledTools.filter(name => name !== tool.name),
                    }));
                  }}
                />
                <span>
                  <strong>{tool.label}</strong>
                  <small>{tool.description}{tool.required ? ' Required.' : ''}</small>
                </span>
              </label>
            ))}
          </div>
        )}

        {profile && tab === 'policies' && (
          <div className="agent-panel">
            <p className="agent-dialog__hint">{copy.policiesHint}</p>
            <ul className="agent-policy-list">
              {draft.policies.map(policy => (
                <li key={policy.id}>
                  <div>
                    <strong>{policy.title}</strong>
                    <small>{policy.jurisdiction} · {policy.source}</small>
                    <p>{policy.body}</p>
                  </div>
                  <button type="button" disabled={locked} onClick={() => setDraft(current => ({ ...current, policies: current.policies.filter(item => item.id !== policy.id) }))}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <label className="field-label">
              Title
              <input value={policyForm.title} disabled={locked} onChange={event => setPolicyForm(current => ({ ...current, title: event.target.value }))} />
            </label>
            <div className="agent-grid">
              <label className="field-label">
                Whose policy
                <select value={policyForm.jurisdiction} disabled={locked} onChange={event => setPolicyForm(current => ({ ...current, jurisdiction: event.target.value as AtlasPolicy['jurisdiction'] }))}>
                  <option value="department">Department</option>
                  <option value="city">City</option>
                  <option value="county">County</option>
                  <option value="state">State</option>
                </select>
              </label>
              <label className="field-label">
                Source
                <input value={policyForm.source} disabled={locked} placeholder={copy.sourcePlaceholder} onChange={event => setPolicyForm(current => ({ ...current, source: event.target.value }))} />
              </label>
            </div>
            <label className="field-label">
              Text
              <textarea rows={5} value={policyForm.body} disabled={locked} onChange={event => setPolicyForm(current => ({ ...current, body: event.target.value }))} />
            </label>
            <button className="button button--secondary" type="button" disabled={locked} onClick={addPolicy}>Add policy note</button>
          </div>
        )}

        {(error || saveError) && <div className="form-error">{error || saveError}</div>}

        <div className="decision-dialog__actions">
          <button className="button button--secondary" type="button" onClick={() => void reset()} disabled={locked || !profile}>Restore defaults</button>
          <button className="button button--secondary" type="button" onClick={onClose} disabled={locked}>Cancel</button>
          <button className="button button--approve" type="button" onClick={() => void save()} disabled={locked || !profile}>
            {saving ? 'Saving…' : copy.saveLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
