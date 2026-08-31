import React from 'react';

/* Presentation only. Values and handlers come from NexusDesk.renderVals(). */

const mark = (row) => (
  <span style={{
    width: '0.5rem',
    height: '0.5rem',
    borderRadius: '50%',
    background: row.markFill || 'transparent',
    border: row.markBorder ? `0.125rem solid ${row.markBorder}` : '0',
  }} />
);

export default function NexusDeskTemplate({ vals }) {
  const v = vals.view;
  return (
    <>
    <div data-screen-label="Operator desk" style={{ width: '100vw', height: '67.5rem', display: 'grid', gridTemplateRows: '3.75rem minmax(0, 1fr) 4rem', background: '#06070A' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', padding: '0 1.25rem', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
        <span style={{ fontFamily: 'Archivo, sans-serif', fontStretch: '100%', fontSize: '1.0625rem', fontWeight: '700', letterSpacing: '0.11em', whiteSpace: 'nowrap' }}>
          NEXUS COORDINATE
        </span>
        <span style={{ width: '0.0625rem', height: '1.25rem', background: 'rgba(255,255,255,0.16)' }}></span>
        <span style={{ fontSize: '0.875rem', color: '#9AA1AB', whiteSpace: 'nowrap' }}>
          {v.eventName}
        </span>
        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', letterSpacing: '0.12em', color: '#F0B429', textTransform: 'uppercase' }}>
          {v.packLine}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ width: '0.4375rem', height: '0.4375rem', borderRadius: '50%', background: v.modeColor, animation: 'nx-live 2s infinite' }}></span>
          <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: v.modeColor }}>
            {v.modeLabel}
          </span>
        </span>
        <span style={{ fontSize: '0.8125rem', color: '#9AA1AB', whiteSpace: 'nowrap' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: '#F4F2ED' }}>{v.feedLive}</span>
          {' of '}
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: '#F4F2ED' }}>{v.feedTotal}</span>
          {' feeds'}
        </span>
        <span style={{ width: '0.0625rem', height: '1.25rem', background: 'rgba(255,255,255,0.16)' }}></span>
        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.25rem', fontWeight: '500', fontVariantNumeric: 'tabular-nums', letterSpacing: '0.02em' }}>
          {vals.clock}
        </span>
        <span style={{ width: '0.0625rem', height: '1.25rem', background: 'rgba(255,255,255,0.16)' }}></span>
        <span style={{ textAlign: 'right', lineHeight: '1.25' }}>
          <span style={{ display: 'block', fontSize: '0.875rem', fontWeight: '600' }}>{v.operatorName}</span>
          <span style={{ display: 'block', fontSize: '0.75rem', color: '#626973' }}>{v.operatorRole}</span>
        </span>
      </header>
      <div style={{ display: 'grid', gridTemplateColumns: '20rem minmax(0, 1fr) 25rem', minHeight: '0' }}>
        <aside style={{ borderRight: '0.0625rem solid rgba(255,255,255,0.10)', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', minHeight: '0' }}>
          <div style={{ padding: '1rem 1.125rem 0.75rem', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
              Waiting on you
            </span>
            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.25rem', color: '#F0B429' }}>
              {v.queue.length}
            </span>
          </div>
          <div style={{ overflow: 'auto', padding: '0 0.75rem 1rem', display: 'grid', gap: '0.5rem', alignContent: 'start' }}>
            {v.queue.length === 0 && (
              <div style={{ padding: '0.875rem', color: '#9AA1AB', fontSize: '0.875rem', lineHeight: '1.35' }}>
                {v.noWindow ? 'No operating window is open.' : 'No recommendation is waiting for a named decision.'}
              </div>
            )}
            {v.queue.map(item => (
              <button
                key={item.id}
                type="button"
                onClick={() => vals.onSelect(item.incidentId)}
                style={{
                  textAlign: 'left',
                  background: item.selected ? 'rgba(240,180,41,0.08)' : '#0B0E13',
                  border: '0',
                  borderLeft: `0.1875rem solid ${item.selected ? '#F0B429' : 'rgba(255,255,255,0.16)'}`,
                  padding: '0.875rem',
                  display: 'grid',
                  gap: '0.5rem',
                  color: 'inherit',
                  fontFamily: 'inherit',
                  cursor: 'pointer',
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span style={{ padding: '0.125rem 0.4375rem', background: item.sevBg, color: '#06070A', fontSize: '0.625rem', fontWeight: '700', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
                    {item.severity}
                  </span>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#9AA1AB' }}>{item.at}</span>
                  <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#F0B429' }}>{item.exp}</span>
                </span>
                <span style={{ fontSize: '1rem', fontWeight: '600', lineHeight: '1.25' }}>{item.title}</span>
                <span style={{ fontSize: '0.8125rem', color: '#9AA1AB', lineHeight: '1.3' }}>{item.place}</span>
                <span style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
                  {item.tags.map(tag => (
                    <span key={tag} style={{ padding: '0.125rem 0.4375rem', border: '0.0625rem solid rgba(255,77,79,0.45)', color: '#FF9799', fontSize: '0.6875rem', fontWeight: '600', whiteSpace: 'nowrap' }}>
                      {tag}
                    </span>
                  ))}
                </span>
              </button>
            ))}
            <div style={{ marginTop: '0.75rem', padding: '0 0.375rem', fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
              Cleared today
            </div>
            {v.cleared.length === 0 && (
              <div style={{ padding: '0.6875rem 0.75rem', color: '#626973', fontSize: '0.8125rem' }}>
                Nothing closed in this window yet.
              </div>
            )}
            {v.cleared.map(item => (
              <div key={item.id} style={{ padding: '0.6875rem 0.75rem', borderLeft: `0.1875rem solid ${item.tone}`, background: '#0B0E13', display: 'grid', gap: '0.1875rem' }}>
                <span style={{ fontSize: '0.8125rem', color: '#F4F2ED', lineHeight: '1.25' }}>{item.title}</span>
                <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.6875rem', color: '#626973' }}>{item.meta}</span>
              </div>
            ))}
          </div>
        </aside>
        <section style={{ minHeight: '0', overflow: 'auto', padding: '1.25rem 1.5rem 1.5rem', display: 'grid', alignContent: 'start', gap: '1.125rem' }}>
          {v.loading && <p style={{ color: '#9AA1AB' }}>Opening the desk…</p>}
          {v.error && <p style={{ color: '#FF9799' }}>{v.error}</p>}
          {v.noWindow ? (
            <div style={{ display: 'grid', gap: '1rem', maxWidth: '36rem' }}>
              <h1 style={{ margin: 0, fontSize: '1.625rem', fontWeight: 600 }}>No operating window is open</h1>
              <p style={{ margin: 0, color: '#9AA1AB', lineHeight: 1.4 }}>
                Detection does not run until a named command lead opens a window and chooses a scenario pack. Nexus records decisions. It does not control a signal or field system.
              </p>
              {v.canManageWindow && v.packs.length > 0 && (
                <>
                  <label style={{ display: 'grid', gap: '0.375rem', fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                    Scenario
                    <select value={vals.packCode} onChange={vals.onPack} style={{ height: '2.75rem', background: '#0B0E13', color: '#F4F2ED', border: '0.0625rem solid rgba(255,255,255,0.16)', fontFamily: 'inherit' }}>
                      {v.packs.map(pack => <option key={pack.packCode} value={pack.packCode}>{pack.name}</option>)}
                    </select>
                  </label>
                  <label style={{ display: 'grid', gap: '0.375rem', fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                    Window name
                    <input value={vals.windowName} onChange={vals.onWindowName} placeholder="Optional" style={{ height: '2.75rem', padding: '0 0.75rem', background: '#0B0E13', color: '#F4F2ED', border: '0.0625rem solid rgba(255,255,255,0.16)', fontFamily: 'inherit' }} />
                  </label>
                  <button type="button" onClick={vals.onOpenWindow} disabled={vals.busy} style={{ height: '3.25rem', background: '#2FD98A', border: 0, color: '#06070A', fontFamily: 'inherit', fontSize: '1rem', fontWeight: 700, cursor: 'pointer' }}>
                    {vals.busy ? 'Opening…' : 'Open operating window'}
                  </button>
                </>
              )}
            </div>
          ) : (
            <>
              <div style={{ display: 'grid', gap: '0.375rem' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                  {v.recVersionLabel} · {v.recMeta}
                </span>
                <h1 style={{ margin: '0', fontSize: '1.625rem', fontWeight: '600', lineHeight: '1.25', textWrap: 'pretty' }}>
                  {v.recAction}
                </h1>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.125rem', padding: '0.875rem 0', borderTop: '0.0625rem solid rgba(255,255,255,0.10)', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
                <div style={{ display: 'grid', gap: '0.3125rem' }}>
                  <span style={{ fontSize: '0.6875rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>Expected effect</span>
                  <span style={{ fontSize: '0.9375rem', lineHeight: '1.35' }}>{v.expectedEffect}</span>
                </div>
                <div style={{ display: 'grid', gap: '0.3125rem' }}>
                  <span style={{ fontSize: '0.6875rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#F0B429' }}>Stated limitations</span>
                  <span style={{ fontSize: '0.9375rem', lineHeight: '1.35', color: '#9AA1AB' }}>{v.limitations}</span>
                </div>
              </div>
              <div style={{ display: 'grid', gap: '0.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                    Six desks, one frozen evidence snapshot
                  </span>
                  <span style={{ fontSize: '0.8125rem', color: '#9AA1AB' }}>
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: '#2FD98A' }}>{v.desksContributed}</span>
                    {' contributed · '}
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: '#626973' }}>{v.abstainedCount}</span>
                    {' abstained · '}
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: '#F0B429' }}>{v.dissentCount}</span>
                    {' dissent'}
                  </span>
                </div>
                <div style={{ display: 'grid', gap: '0.1875rem' }}>
                  {v.desks.map(row => (
                    <div key={row.code} style={{ display: 'grid', gridTemplateColumns: '6rem minmax(0, 1fr) 8rem 9.75rem', gap: '0.875rem', alignItems: 'center', padding: '0.5625rem 0.75rem', background: row.rowBg, borderLeft: `0.1875rem solid ${row.hue}` }}>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.875rem', fontWeight: '700', letterSpacing: '0.05em', color: row.nameColor }}>{row.name}</span>
                      <span style={{ fontSize: '0.875rem', lineHeight: '1.3', color: row.lineColor }}>{row.line}</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '0.4375rem' }}>
                        {mark(row)}
                        <span style={{ fontSize: '0.8125rem', fontWeight: '600', color: row.statusColor }}>{row.statusLabel}</span>
                      </span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#626973', textAlign: 'right' }}>{row.meta}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ display: 'grid', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                  Cited evidence · {v.evidenceFrozen} · sha256 {v.hashShort}
                </span>
                <div style={{ display: 'grid', gap: '0.125rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem' }}>
                  {v.evidence.length === 0 && (
                    <div style={{ padding: '0.4375rem 0.75rem', background: '#0B0E13', color: '#626973' }}>No evidence is cited on this recommendation.</div>
                  )}
                  {v.evidence.map(row => (
                    <div key={row.id} style={{ display: 'grid', gridTemplateColumns: '5.5rem 13.5rem minmax(0, 1fr) 4.5rem', gap: '0.875rem', padding: '0.4375rem 0.75rem', background: '#0B0E13', color: '#9AA1AB' }}>
                      <span style={{ color: '#F4F2ED' }}>{row.short}</span>
                      <span>{row.source}</span>
                      <span style={{ color: '#F4F2ED' }}>{row.summary}</span>
                      <span style={{ textAlign: 'right' }}>{row.at}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ display: 'grid', gap: '0.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
                    Commitments on this incident
                  </span>
                  <span style={{ fontSize: '0.8125rem', color: '#626973' }}>
                    Approval records responsibility — no agency system is actuated
                  </span>
                </div>
                <div style={{ display: 'grid', gap: '0.125rem' }}>
                  {v.commitmentPreview.length === 0 && (
                    <div style={{ padding: '0.5rem 0.75rem', background: '#0B0E13', color: '#9AA1AB', fontSize: '0.875rem' }}>
                      None yet. They appear after a named person signs, not before.
                    </div>
                  )}
                  {v.commitmentPreview.map(row => (
                    <div key={row.id} style={{ display: 'grid', gridTemplateColumns: '14rem minmax(0, 1fr) 9rem 5rem', gap: '0.875rem', alignItems: 'center', padding: '0.5rem 0.75rem', background: '#0B0E13' }}>
                      <span style={{ fontSize: '0.875rem', fontWeight: '600' }}>{row.agency}</span>
                      <span style={{ fontSize: '0.875rem', color: '#9AA1AB', lineHeight: '1.3' }}>{row.outcome}</span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#626973' }}>{row.owner}</span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#626973', textAlign: 'right' }}>{row.due}</span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </section>
        <aside style={{ borderLeft: '0.0625rem solid rgba(255,255,255,0.10)', background: '#0B0E13', minHeight: '0', overflowY: 'auto', overflowX: 'hidden', display: 'grid', alignContent: 'start', gap: '0.875rem', padding: '1.25rem 1.25rem 1.5rem' }}>
          <div style={{ display: 'grid', gap: '0.25rem' }}>
            <span style={{ fontSize: '0.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#F0B429' }}>
              Sign the decision
            </span>
            <span style={{ fontSize: '0.8125rem', color: '#9AA1AB', lineHeight: '1.35' }}>
              The wall witnesses this. Only the desk can write it.
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '8.75rem minmax(0, 1fr)', gap: '0.4375rem 0.75rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', padding: '0.75rem 0', borderTop: '0.0625rem solid rgba(255,255,255,0.10)', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
            <span style={{ color: '#626973' }}>approver</span><span>{v.operatorName}</span>
            <span style={{ color: '#626973' }}>agency</span><span>{v.agencyName}</span>
            <span style={{ color: '#626973' }}>expected version</span><span>{v.recVersion}</span>
            <span style={{ color: '#626973' }}>expected state</span><span>{v.recState}</span>
            <span style={{ color: '#626973' }}>snapshot sha256</span><span>{v.hashShort}</span>
          </div>
          <div style={{ display: 'grid', gap: '0.3125rem' }}>
            <span style={{ fontSize: '0.6875rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973' }}>
              Your reason · recorded in the audit log
            </span>
            <textarea
              value={vals.reason}
              onChange={vals.onReason}
              placeholder="What you reviewed. Required when sending back, escalating, or declining."
              style={{ minHeight: '4.5rem', padding: '0.625rem 0.75rem', background: '#06070A', border: '0.0625rem solid rgba(255,255,255,0.14)', fontSize: '0.875rem', color: '#F4F2ED', lineHeight: '1.35', fontFamily: 'inherit', resize: 'vertical' }}
            />
          </div>
          <button type="button" onClick={vals.onConfirm} style={{ display: 'grid', gridTemplateColumns: '1.125rem minmax(0, 1fr)', gap: '0.625rem', alignItems: 'start', padding: '0.6875rem 0.75rem', border: '0.0625rem solid rgba(240,180,41,0.4)', background: vals.confirmed ? 'rgba(240,180,41,0.07)' : 'transparent', color: 'inherit', fontFamily: 'inherit', textAlign: 'left', cursor: 'pointer' }}>
            <span style={{ width: '1.125rem', height: '1.125rem', border: '0.125rem solid #F0B429', background: vals.confirmed ? '#F0B429' : 'transparent', display: 'grid', placeContent: 'center', color: '#06070A', fontSize: '0.75rem', fontWeight: '700' }}>
              {vals.confirmed ? '✓' : ''}
            </span>
            <span style={{ fontSize: '0.8125rem', color: '#F4F2ED', lineHeight: '1.35' }}>{vals.confirmLabel}</span>
          </button>
          {vals.message && <div style={{ padding: '0.625rem 0.75rem', border: '0.0625rem solid rgba(255,77,79,0.5)', color: '#FF9799', fontSize: '0.8125rem' }}>{vals.message}</div>}
          <div style={{ display: 'grid', gap: '0.5rem' }}>
            <button type="button" onClick={vals.onApprove} disabled={!v.canDecide || vals.busy} style={{ height: '3.25rem', background: '#2FD98A', border: '0', color: '#06070A', fontFamily: 'inherit', fontSize: '1rem', fontWeight: '700', cursor: v.canDecide ? 'pointer' : 'not-allowed', opacity: v.canDecide ? 1 : 0.45 }}>
              {vals.busy ? 'Recording…' : vals.approveLabel}
            </button>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              <button type="button" onClick={vals.onSendBack} disabled={!v.canDecide || vals.busy} style={{ height: '2.75rem', background: '#12151B', border: '0.0625rem solid rgba(255,255,255,0.16)', color: '#F4F2ED', fontFamily: 'inherit', fontSize: '0.875rem', fontWeight: '600', cursor: 'pointer' }}>
                Send back
              </button>
              <button type="button" onClick={vals.onEscalate} disabled={!v.canDecide || vals.busy} style={{ height: '2.75rem', background: '#12151B', border: '0.0625rem solid rgba(255,255,255,0.16)', color: '#F4F2ED', fontFamily: 'inherit', fontSize: '0.875rem', fontWeight: '600', cursor: 'pointer' }}>
                Escalate
              </button>
            </div>
            <button type="button" onClick={vals.onDecline} disabled={!v.canDecide || vals.busy} style={{ height: '2.75rem', background: 'rgba(255,77,79,0.12)', border: '0.0625rem solid rgba(255,77,79,0.5)', color: '#FF9799', fontFamily: 'inherit', fontSize: '0.875rem', fontWeight: '600', cursor: 'pointer' }}>
              Decline
            </button>
          </div>
          <span style={{ fontSize: '0.75rem', color: '#626973', lineHeight: '1.4' }}>
            Approval creates accountable agency commitments. It does not change a signal, close a road, publish a sign, or dispatch a crew.
          </span>
        </aside>
      </div>
      <footer style={{ display: 'flex', alignItems: 'center', gap: '0', padding: '0 1.25rem', borderTop: '0.0625rem solid rgba(255,255,255,0.10)', overflow: 'hidden' }}>
        <span style={{ fontSize: '0.6875rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: '#626973', marginRight: '1.125rem', whiteSpace: 'nowrap' }}>
          Feeds
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1.375rem', overflow: 'hidden' }}>
          {v.feeds.length === 0 && <span style={{ fontSize: '0.8125rem', color: '#626973' }}>No feeds in this window.</span>}
          {v.feeds.map(feed => (
            <span key={feed.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4375rem', whiteSpace: 'nowrap' }}>
              <span style={{ width: '0.4375rem', height: '0.4375rem', borderRadius: '50%', background: feed.dot }}></span>
              <span style={{ fontSize: '0.8125rem', color: feed.muted ? '#9AA1AB' : '#F4F2ED' }}>{feed.name}</span>
              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '0.75rem', color: '#626973' }}>{feed.lag}</span>
            </span>
          ))}
        </div>
      </footer>
    </div>
    </>
  );
}
