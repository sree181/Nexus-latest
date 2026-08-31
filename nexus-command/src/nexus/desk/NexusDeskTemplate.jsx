/**
 * CIVIC INSTRUMENT PANEL
 * Asymmetric command frame, indexed sections, signal rails, and restrained cartographic texture.
 */
import React from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  FileCheck2,
  Gauge,
  Layers3,
  Radio,
  Route,
  Send,
  ShieldAlert,
  Undo2,
  UsersRound,
} from 'lucide-react';

const LOGO_URL = '/manus-storage/nexus-junction-mark_0614c4a0.png';
const EVIDENCE_ART_URL = '/manus-storage/nexus-evidence-lineage_c2d30e5c.jpg';

const SectionLabel = ({ index, children, tone = 'muted' }) => (
  <div className={`nx-section-label nx-section-label--${tone}`}>
    <span>{index}</span>
    <span>{children}</span>
  </div>
);

const StatusMark = ({ row }) => (
  <span
    className="nx-status-mark"
    style={{
      '--mark-fill': row.markFill || 'transparent',
      '--mark-border': row.markBorder || row.statusColor || 'currentColor',
    }}
    aria-hidden="true"
  />
);

export default function NexusDeskTemplate({ vals }) {
  const v = vals.view;

  return (
    <div className="nx-desk" data-screen-label="Operator desk">
      <header className="nx-topbar">
        <div className="nx-brand-lockup">
          <span className="nx-brand-symbol" aria-hidden="true">
            <img className="nx-brand-mark" src={LOGO_URL} alt="" onError={(event) => { event.currentTarget.style.opacity = '0'; }} />
          </span>
          <div><strong className="nx-wordmark" aria-label="Nexus">NE<span>X</span>US</strong><span>Coordinate</span></div>
        </div>
        <div className="nx-mission-context">
          <span className="nx-kicker">Active operating window</span>
          <strong>{v.eventName}</strong>
          <span className="nx-mono">{v.packLine}</span>
        </div>
        <div className="nx-topbar-status" aria-label={`${v.modeLabel}, ${v.feedLive} of ${v.feedTotal} feeds`}>
          <span className="nx-live-beacon" style={{ '--beacon': v.modeColor }} />
          <div><strong style={{ color: v.modeColor }}>{v.modeLabel}</strong><span>{v.feedLive} / {v.feedTotal} feeds</span></div>
        </div>
        <a className="nx-wall-link" href="/wall.html">Command wall <ExternalLink size={15} strokeWidth={1.8} /></a>
        <div className="nx-clock" aria-label={`Current time ${vals.clock}`}><span>{vals.date}</span><strong>{vals.clock}</strong></div>
        <div className="nx-operator">
          <span className="nx-operator-avatar" aria-hidden="true">{v.operatorName?.split(' ').map(part => part[0]).slice(0, 2).join('')}</span>
          <span><strong>{v.operatorName}</strong><small>{v.operatorRole}</small></span>
        </div>
      </header>

      <div className="nx-command-frame">
        <aside className="nx-queue-rail" aria-label="Incident queue">
          <div className="nx-rail-heading"><SectionLabel index="01">Attention queue</SectionLabel><span className="nx-count-badge">{v.queue.length}</span></div>
          <p className="nx-rail-intro">Decisions waiting for a named operator.</p>
          <div className="nx-queue-list">
            {v.queue.length === 0 && (
              <div className="nx-empty-state"><CheckCircle2 size={20} /><strong>{v.noWindow ? 'No operating window' : 'Queue is clear'}</strong><span>{v.noWindow ? 'Open a scenario to begin monitoring.' : 'No recommendation needs a named decision.'}</span></div>
            )}
            {v.queue.map((item, index) => (
              <button key={item.id} type="button" className={`nx-queue-card${item.selected ? ' is-selected' : ''}`} onClick={() => vals.onSelect(item.incidentId)} aria-pressed={item.selected} style={{ '--severity': item.sevBg }}>
                <span className="nx-queue-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="nx-queue-meta"><span className="nx-severity-pill">{item.severity}</span><span className="nx-mono">{item.at}</span><span className="nx-expiry"><Clock3 size={12} /> {item.exp}</span></span>
                <strong>{item.title}</strong>
                <span className="nx-queue-place"><Route size={14} /> {item.place}</span>
                <span className="nx-tag-row">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</span>
                <ArrowUpRight className="nx-card-arrow" size={17} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div className="nx-cleared-block">
            <div className="nx-subheading"><span>Cleared today</span><span>{v.cleared.length}</span></div>
            {v.cleared.length === 0 && <p>Nothing closed in this window yet.</p>}
            {v.cleared.map(item => <div key={item.id} className="nx-cleared-row" style={{ '--tone': item.tone }}><strong>{item.title}</strong><span className="nx-mono">{item.meta}</span></div>)}
          </div>
        </aside>

        <main className="nx-workspace">
          {v.loading && <div className="nx-loading"><Radio size={18} /> Opening the desk…</div>}
          {v.error && <div className="nx-inline-alert"><AlertTriangle size={18} /> {v.error}</div>}
          {v.noWindow ? (
            <section className="nx-window-empty">
              <div><SectionLabel index="02" tone="orange">Start an operating window</SectionLabel><h1>Give the room a named operational context.</h1><p>Detection begins only after a command lead selects a scenario pack and opens a window. Nexus records accountable decisions; it does not actuate field systems.</p></div>
              {v.canManageWindow && v.packs.length > 0 && (
                <div className="nx-window-form">
                  <label><span>Scenario pack</span><select value={vals.packCode} onChange={vals.onPack}>{v.packs.map(pack => <option key={pack.packCode} value={pack.packCode}>{pack.name}</option>)}</select></label>
                  <label><span>Window name</span><input value={vals.windowName} onChange={vals.onWindowName} placeholder="Optional operational label" /></label>
                  <button className="nx-primary-button" type="button" onClick={vals.onOpenWindow} disabled={vals.busy}>{vals.busy ? 'Opening…' : 'Open operating window'}<ArrowUpRight size={18} /></button>
                </div>
              )}
            </section>
          ) : (
            <>
              <section className="nx-decision-brief">
                <div className="nx-brief-copy">
                  <SectionLabel index="02" tone="orange">Decision brief</SectionLabel>
                  <div className="nx-brief-meta"><span className="nx-mono">{v.recVersionLabel}</span><span>{v.recMeta}</span></div>
                  <h1>{v.recAction}</h1>
                  <div className="nx-brief-facts">
                    <div><Gauge size={17} /><span><small>Expected effect</small><strong>{v.expectedEffect}</strong></span></div>
                    <div className="is-warning"><ShieldAlert size={17} /><span><small>Stated limitations</small><strong>{v.limitations}</strong></span></div>
                  </div>
                </div>
                <div className="nx-brief-art" aria-hidden="true"><img src={EVIDENCE_ART_URL} alt="" /><div className="nx-art-caption"><span>Frozen evidence path</span><span className="nx-mono">sha256 {v.hashShort}</span></div></div>
              </section>

              <section className="nx-section nx-desk-section">
                <div className="nx-section-head">
                  <div><SectionLabel index="03">Agency review</SectionLabel><h2>Six desks, one accountable recommendation.</h2></div>
                  <div className="nx-stat-cluster" aria-label={`${v.desksContributed} contributed, ${v.abstainedCount} abstained, ${v.dissentCount} dissent`}><span><strong>{v.desksContributed}</strong> contributed</span><span><strong>{v.abstainedCount}</strong> abstained</span><span className="is-orange"><strong>{v.dissentCount}</strong> dissent</span></div>
                </div>
                <div className="nx-desk-table">
                  {v.desks.map(row => (
                    <div key={row.code} className={`nx-desk-row${row.statusLabel === 'Dissent' ? ' is-dissent' : ''}`} style={{ '--desk-hue': row.hue, '--desk-bg': row.rowBg }}>
                      <div className="nx-desk-name"><span className="nx-desk-code" style={{ color: row.nameColor }}>{row.name}</span><span className="nx-mono">{row.code}</span></div>
                      <p style={{ color: row.lineColor }}>{row.line}</p>
                      <div className="nx-desk-status" style={{ color: row.statusColor }}><StatusMark row={row} /><span>{row.statusLabel}</span></div>
                      <span className="nx-mono nx-desk-meta">{row.meta}</span>
                    </div>
                  ))}
                </div>
              </section>

              <section className="nx-section nx-proof-section">
                <div className="nx-section-head"><div><SectionLabel index="03A">Evidence snapshot</SectionLabel><h2>Proof attached to this exact version.</h2></div><span className="nx-proof-hash"><FileCheck2 size={15} /> {v.evidenceFrozen} · <span className="nx-mono">{v.hashShort}</span></span></div>
                <div className="nx-evidence-list">
                  {v.evidence.length === 0 && <div className="nx-empty-row">No evidence is cited on this recommendation.</div>}
                  {v.evidence.map((row, index) => (
                    <div key={row.id} className="nx-evidence-row"><span className="nx-evidence-number">{String(index + 1).padStart(2, '0')}</span><span className="nx-mono nx-evidence-id">{row.short}</span><span className="nx-evidence-source">{row.source}</span><strong>{row.summary}</strong><span className="nx-mono nx-evidence-time">{row.at}</span></div>
                  ))}
                </div>
              </section>

              <section className="nx-section nx-commitment-section">
                <div className="nx-section-head"><div><SectionLabel index="05">Accountable follow-through</SectionLabel><h2>Commitments on this incident.</h2></div><span className="nx-caption">Approval records responsibility—no agency system is actuated.</span></div>
                <div className="nx-commitment-list">
                  {v.commitmentPreview.length === 0 && <div className="nx-empty-row"><UsersRound size={16} /> None yet. Commitments appear only after a named person signs.</div>}
                  {v.commitmentPreview.map(row => <div key={row.id} className="nx-commitment-row"><strong>{row.agency}</strong><span>{row.outcome}</span><span className="nx-mono">{row.owner}</span><span className="nx-mono">{row.due}</span></div>)}
                </div>
              </section>
            </>
          )}
        </main>

        <aside className="nx-decision-rail" aria-label="Sign the decision">
          <div className="nx-decision-heading"><SectionLabel index="04" tone="orange">Sign the decision</SectionLabel><h2>Make the record explicit.</h2><p>The wall witnesses this decision. Only the named operator at this desk can write it.</p></div>
          <div className="nx-accountability-card">
            <div><span>Approver</span><strong>{v.operatorName}</strong></div><div><span>Agency</span><strong>{v.agencyName}</strong></div><div><span>Expected version</span><strong className="nx-mono">{v.recVersion}</strong></div><div><span>Expected state</span><strong className="nx-mono">{v.recState}</strong></div><div><span>Snapshot</span><strong className="nx-mono">{v.hashShort}</strong></div>
          </div>
          <label className="nx-reason-field"><span>Your reason <small>Recorded in the audit log</small></span><textarea value={vals.reason} onChange={vals.onReason} placeholder="What you reviewed. Required when sending back, escalating, or declining." /></label>
          <button type="button" className={`nx-confirm-control${vals.confirmed ? ' is-confirmed' : ''}`} onClick={vals.onConfirm} aria-pressed={vals.confirmed}><span className="nx-check-box">{vals.confirmed && <Check size={14} strokeWidth={3} />}</span><span>{vals.confirmLabel}</span></button>
          {vals.message && <div className="nx-decision-message"><AlertTriangle size={16} /> {vals.message}</div>}
          <div className="nx-action-stack">
            <button className="nx-approve-button" type="button" onClick={vals.onApprove} disabled={!v.canDecide || vals.busy}><CheckCircle2 size={19} /><span>{vals.busy ? 'Recording…' : vals.approveLabel}</span></button>
            <div className="nx-secondary-actions"><button type="button" onClick={vals.onSendBack} disabled={!v.canDecide || vals.busy} title="Send the recommendation back for revision"><Undo2 size={16} /> Send back</button><button type="button" onClick={vals.onEscalate} disabled={!v.canDecide || vals.busy} title="Escalate to the named authority"><Send size={16} /> Escalate</button></div>
            <button className="nx-decline-button" type="button" onClick={vals.onDecline} disabled={!v.canDecide || vals.busy}><ShieldAlert size={17} /> Decline with reason</button>
          </div>
          <p className="nx-consequence-note">Approval creates accountable agency commitments. It does not change a signal, close a road, publish a sign, or dispatch a crew.</p>
        </aside>
      </div>

      <footer className="nx-feed-footer">
        <div className="nx-feed-label"><Layers3 size={15} /> Feeds</div>
        <div className="nx-feed-strip">{v.feeds.length === 0 && <span>No feeds in this window.</span>}{v.feeds.map(feed => <span key={feed.key} className={feed.muted ? 'is-muted' : ''}><CircleDot size={13} style={{ color: feed.dot }} /><strong>{feed.name}</strong><span className="nx-mono">{feed.lag}</span></span>)}</div>
      </footer>
    </div>
  );
}
