import React from 'react';
import WorkflowBoard from './workflow/WorkflowBoard';

/* CIVIC INSTRUMENT PANEL: presentation only; all live values and handlers come from NexusWall.renderVals(). */
export default function NexusWallTemplate({ vals }) {
  const screenClass = vals.isOps
    ? 'nx-wall--ops'
    : vals.isEvidence
      ? 'nx-wall--evidence'
      : vals.isWorkflow
        ? 'nx-wall--workflow'
        : vals.isDelib
          ? 'nx-wall--deliberation'
          : vals.isDecision
            ? 'nx-wall--decision'
            : 'nx-wall--commitments';
  return (
    <>
    <div className={`nx-wall ${screenClass}`} data-screen-label="Command wall" style={{ width: '100vw', height: '135rem', maxHeight: '100vh', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr) auto auto auto', background: 'var(--nx-ground)', position: 'relative', overflow: 'hidden' }}>
      <header className="nx-wall-header" data-screen-label="Stratum 1 — state" style={{ padding: '1.75rem 3rem 1.5rem', display: 'grid', gap: '1.25rem', borderBottom: '0.0625rem solid var(--nx-line)' }}>
        <div className="nx-wall-brandrow" style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
          <span className="nx-wall-mark" style={{ flex: 'none', padding: '0.4375rem 0.625rem', display: 'flex', alignItems: 'center' }}>
            <img src="/manus-storage/nexus-junction-mark_0614c4a0.png" alt="" onError={(event) => { event.currentTarget.style.opacity = '0'; }} style={{ height: '3.5rem', width: 'auto', display: 'block' }} />
          </span>
          <span style={{ display: 'grid', gap: '0.125rem', minWidth: 0 }}>
            <span className="nx-wall-wordmark" aria-label="Nexus" style={{ fontFamily: 'Archivo, sans-serif', fontSize: '3.25rem', fontWeight: '700', letterSpacing: '0.08em', lineHeight: '1', color: 'var(--nx-ink)', whiteSpace: 'nowrap' }}>
              NE<i>X</i>US
            </span>
            <span style={{ fontSize: '1.625rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap' }}>
              Coordinate · mobility command
            </span>
          </span>
          <span style={{ marginLeft: 'auto', flex: 'none', display: 'flex', alignItems: 'center', gap: '2rem' }}>
            <a href="/" style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.75rem', fontWeight: '600', color: 'var(--nx-accent)', whiteSpace: 'nowrap' }}>
              Desk
            </a>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <span style={{ width: '0.75rem', height: '0.75rem', borderRadius: '50%', background: 'var(--nx-accent)', animation: 'nx-live 2s infinite' }}></span>
              <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-accent)' }}>
                {vals.modeLive || 'live'}
              </span>
            </span>
            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '3rem', fontWeight: '500', lineHeight: '1', color: 'var(--nx-ink)', fontVariantNumeric: 'tabular-nums' }}>
              {vals.clock}
            </span>
          </span>
        </div>
        <div className="nx-wall-kpis" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: '1rem' }}>
          {[
            { label: 'Feeds', value: vals.feedLive, unit: `/ ${vals.feedTotal}`, color: 'var(--nx-ink)', note: vals.feedDegraded },
            { label: 'Evidence', value: vals.evidenceCount, unit: '', color: 'var(--nx-ink)', note: vals.evidenceFrozen },
            { label: 'Desks', value: vals.desksContributed, unit: vals.desksStaffed, color: 'var(--nx-ink)', note: vals.dissentLine },
            { label: 'Window', value: vals.windowMinutes, unit: vals.windowUnit, color: vals.windowColor || 'var(--nx-accent)', note: vals.recStatusLine },
            { label: 'Signed', value: vals.commitmentsExecuting, unit: vals.commitmentsAccepted, color: 'var(--nx-ink)', note: vals.blockedLine },
          ].map((card) => (
            <div className="nx-wall-kpi" key={card.label} style={{ background: 'var(--nx-surface)', border: '0.0625rem solid var(--nx-line)', padding: '1.125rem 1.5rem', display: 'grid', gap: '0.5rem' }}>
              <span style={{ fontSize: '1.5rem', fontWeight: '600', color: 'var(--nx-mute)' }}>
                {card.label}
              </span>
              <span style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem' }}>
                <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '3.75rem', fontWeight: '700', lineHeight: '0.95', color: card.color, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.03em' }}>
                  {card.value}
                </span>
                {card.unit ? (
                  <span style={{ fontSize: '1.75rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap' }}>
                    {card.unit}
                  </span>
                ) : null}
              </span>
              <span style={{ fontSize: '1.5rem', color: 'var(--nx-faint)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {card.note}
              </span>
            </div>
          ))}
        </div>
      </header>
      <main className="nx-wall-main" data-screen-label="Stratum 2 — the picture" style={{ position: 'relative', overflow: 'hidden', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
        {vals.isOps ? (
          <>
            <div className="nx-wall-picture" style={{ position: 'absolute', inset: '0', display: 'grid', gridTemplateColumns: '92rem minmax(0, 1fr)' }}>
              <section className="nx-wall-priority" data-screen-label="Priority card" style={{ background: 'var(--nx-surface)', borderRight: '0.0625rem solid var(--nx-line)', display: 'grid', overflow: 'hidden' }}>
                <div style={{ display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr) auto', minHeight: '0', height: '100%', borderLeft: '0.5rem solid var(--nx-accent)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap', padding: '1.75rem 2.5rem 1.5rem', borderBottom: '0.0625rem solid var(--nx-line)' }}>
                    <span style={{ flex: 'none', background: 'var(--nx-accent)', color: 'var(--nx-accent-ink)', padding: '0.5rem 0.875rem', fontFamily: 'Archivo, sans-serif', fontSize: '1.375rem', fontWeight: '700', letterSpacing: '0.1em', lineHeight: '1' }}>
                      {vals.sevLabel}
                    </span>
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', color: 'var(--nx-mute)', letterSpacing: '0.04em' }}>
                      {vals.incidentIdLine}
                    </span>
                  </div>
                  <div style={{ display: 'grid', alignContent: 'start', gap: '2rem', minHeight: '0', overflow: 'auto', padding: '2.25rem 2.5rem' }}>
                    <div style={{ display: 'grid', gap: '0.875rem' }}>
                      <div style={{ fontFamily: 'Archivo, sans-serif', fontSize: '3rem', fontWeight: '650', lineHeight: '1.12', letterSpacing: '-0.03em', color: 'var(--nx-ink)', textWrap: 'pretty', overflowWrap: 'break-word', minWidth: 0 }}>
                        {vals.incidentTitle}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', fontSize: '1.625rem', color: 'var(--nx-mute)' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.25rem', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--nx-faint)' }}>
                          Owner
                        </span>
                        <span>{vals.incidentOwner}</span>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gap: '0.75rem', background: 'var(--nx-raised)', padding: '1.5rem 1.75rem', borderTop: '0.25rem solid var(--nx-accent)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.25rem', fontWeight: '700', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--nx-accent)' }}>
                          Ask
                        </span>
                        {(vals.recVersionLabel || vals.recMeta) ? (
                          <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.375rem', color: 'var(--nx-mute)' }}>
                            {[vals.recVersionLabel, vals.recMeta].filter(Boolean).join(' · ')}
                          </span>
                        ) : null}
                      </div>
                      <div style={{ fontFamily: 'Archivo, sans-serif', fontSize: '2rem', fontWeight: '500', lineHeight: '1.3', letterSpacing: '-0.015em', color: 'var(--nx-ink)', textWrap: 'pretty', overflowWrap: 'break-word', minWidth: 0 }}>
                        {vals.recAction}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gap: '1rem', padding: '1.5rem 2.5rem 1.75rem', borderTop: '0.0625rem solid var(--nx-line)', background: 'var(--nx-raised)' }}>
                    {vals.awaiting ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', background: 'var(--nx-accent)', color: 'var(--nx-accent-ink)', padding: '0.875rem 1.25rem' }}>
                        <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.75rem', fontWeight: '700', lineHeight: '1' }}>
                          {vals.awaitBanner}
                        </span>
                        <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', fontWeight: '600', fontVariantNumeric: 'tabular-nums', lineHeight: '1' }}>
                          {vals.awaitClock}
                        </span>
                      </div>
                    ) : null}
                    {vals.signed ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', background: 'var(--nx-ink)', color: 'var(--nx-ground)', padding: '0.875rem 1.25rem' }}>
                        <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.75rem', fontWeight: '700', lineHeight: '1' }}>
                          {vals.signedBanner}
                        </span>
                        <span style={{ marginLeft: 'auto', fontSize: '1.625rem', fontWeight: '600', lineHeight: '1' }}>
                          {vals.signedMeta}
                        </span>
                      </div>
                    ) : null}
                    <button className="nxw-h1" type="button" onClick={vals.togglePriority} style={{ justifySelf: 'stretch', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: '0.125rem solid var(--nx-accent)', color: 'var(--nx-accent)', fontFamily: 'Archivo, sans-serif', fontSize: '1.625rem', fontWeight: '650', lineHeight: '1', padding: '0.875rem 1.25rem', cursor: 'pointer' }}>
                      {vals.priorityLabel}
                    </button>
                  </div>
                </div>
              </section>
              {vals.priorityOpen ? (
                <>
                  <div style={{ position: 'absolute', inset: '0', zIndex: '20', background: 'rgba(4,5,8,0.9)', display: 'grid', placeItems: 'center', padding: '2.5rem 4rem' }}>
                    <div data-screen-label="Priority card expanded" style={{ width: '100%', maxWidth: '210rem', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.22)', display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.15fr)' }}>
                      <div style={{ display: 'grid', alignContent: 'start', gap: '1.5rem', padding: '2.5rem 3rem', borderRight: '0.0625rem solid rgba(255,255,255,0.12)', borderLeft: '0.625rem solid var(--nx-accent)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', flexWrap: 'wrap' }}>
                          <span style={{ flex: 'none', background: vals.sevBg, color: 'var(--nx-ground)', padding: '0.4375rem 0.875rem', fontFamily: 'Archivo, sans-serif', fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.08em', lineHeight: '1', whiteSpace: 'nowrap' }}>
                            {vals.sevLabel}
                          </span>
                          <span style={{ fontSize: '1.875rem', fontWeight: '500', color: 'var(--nx-mute)', lineHeight: '1.3' }}>
                            {vals.incidentIdLine}
                          </span>
                        </div>
                        <div style={{ fontFamily: 'Archivo, sans-serif', fontSize: '3.25rem', fontWeight: '650', lineHeight: '1.18', letterSpacing: '-0.02em', color: 'var(--nx-ink)', textWrap: 'pretty', overflowWrap: 'break-word', minWidth: 0 }}>
                          {vals.incidentTitle}
                        </div>
                        <div style={{ fontSize: '2rem', lineHeight: '1.35', color: 'var(--nx-mute)' }}>
                          {vals.incidentOwner}
                        </div>
                        <div style={{ display: 'grid', gap: '1.125rem', paddingTop: '1.5rem', borderTop: '0.0625rem solid rgba(255,255,255,0.12)' }}>
                          <div style={{ display: 'grid', gap: '0.4375rem' }}>
                            <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.625rem', fontWeight: '700', letterSpacing: '0.04em', color: 'var(--nx-accent)' }}>
                              {vals.recVersionLabel}
                            </span>
                            <span style={{ fontSize: '1.75rem', lineHeight: '1.35', color: 'var(--nx-mute)' }}>
                              {vals.recMeta}
                            </span>
                          </div>
                          <div style={{ fontFamily: 'Archivo, sans-serif', fontSize: '2.125rem', fontWeight: '500', lineHeight: '1.32', letterSpacing: '-0.01em', color: 'var(--nx-ink)', textWrap: 'pretty', overflowWrap: 'break-word', minWidth: 0 }}>
                            {vals.recAction}
                          </div>
                          {vals.awaiting ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', flexWrap: 'wrap', background: 'var(--nx-accent)', color: 'var(--nx-ground)', padding: '0.875rem 1.5rem' }}>
                              <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '2rem', fontWeight: '700', lineHeight: '1.2' }}>
                                {vals.awaitBanner}
                              </span>
                              <span style={{ marginLeft: 'auto', fontSize: '1.75rem', fontWeight: '600', lineHeight: '1.2', whiteSpace: 'nowrap' }}>
                                {vals.awaitClock}
                              </span>
                            </div>
                          ) : null}
                        </div>
                      </div>
                      <div style={{ display: 'grid', alignContent: 'start', gap: '1.5rem', padding: '2.5rem 3rem', background: '#090C11' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
                          <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '1.875rem', fontWeight: '700', letterSpacing: '0.04em', color: 'var(--nx-mute)', whiteSpace: 'nowrap' }}>
                            The basis
                          </span>
                          <button className="nxw-h2" type="button" onClick={vals.togglePriority} style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', background: 'transparent', border: '0.0625rem solid rgba(255,255,255,0.24)', color: 'var(--nx-ink)', fontFamily: 'Archivo, sans-serif', fontSize: '1.625rem', fontWeight: '600', letterSpacing: '0.02em', lineHeight: '1', padding: '0.75rem 1.25rem', whiteSpace: 'nowrap', cursor: 'pointer' }}>
                            Close
                          </button>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '16rem minmax(0, 1fr)', gap: '1.125rem 2rem', alignItems: 'start' }}>
                          <span style={{ fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-faint)', paddingTop: '0.125rem' }}>
                            Snapshot
                          </span>
                          <span style={{ fontSize: '1.875rem', lineHeight: '1.35', color: 'var(--nx-ink)' }}>
                            {vals.snapshotBasis}
                          </span>
                          <span style={{ fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-faint)', paddingTop: '0.125rem' }}>
                            Desks
                          </span>
                          <span style={{ fontSize: '1.875rem', lineHeight: '1.35', color: 'var(--nx-ink)' }}>
                            {vals.deskStrip}
                          </span>
                          <span style={{ fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-accent)', paddingTop: '0.125rem' }}>
                            Dissent
                          </span>
                          <span style={{ fontSize: '1.875rem', lineHeight: '1.35', color: 'var(--nx-ink)', textWrap: 'pretty' }}>
                            {vals.dissentNote}
                          </span>
                          <span style={{ fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-faint)', paddingTop: '0.125rem' }}>
                            Limitation
                          </span>
                          <span style={{ fontSize: '1.875rem', lineHeight: '1.35', color: '#B6BAC1', textWrap: 'pretty' }}>
                            {vals.limitations}
                          </span>
                          <span style={{ fontSize: '1.625rem', fontWeight: '600', color: 'var(--nx-faint)', paddingTop: '0.125rem' }}>
                            Signing
                          </span>
                          <span style={{ fontSize: '1.875rem', lineHeight: '1.35', color: '#B6BAC1', textWrap: 'pretty' }}>
                            Requires a named human at the desk with expected version {vals.recVersion} and this snapshot hash. The wall cannot sign; approval records responsibility, not execution.
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </>
              ) : null}
              <div className="nx-wall-map" data-screen-label="Incident map" style={{ position: 'relative', zIndex: '0', isolation: 'isolate', overflow: 'hidden' }}>
                <div ref={vals.mapRef} style={{ position: 'absolute', inset: '0', background: 'var(--nx-ground)', filter: 'brightness(0.45) saturate(0.62)' }}></div>
                {vals.isWalkUp ? (
                  <>
                    <button type="button" onClick={vals.resetMap} style={{ position: 'absolute', right: '2.5rem', bottom: '2.5rem', background: 'rgba(6,7,10,0.86)', border: '0.0625rem solid rgba(255,255,255,0.28)', color: 'var(--nx-ink)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', letterSpacing: '0.12em', padding: '0.875rem 1.75rem', cursor: 'pointer' }}>
                      Reset
                    </button>
                  </>
                ) : null}
                <div data-nx-overlays style={{ position: 'absolute', right: '2.5rem', top: '2.5rem', width: '56rem', display: 'grid', justifyItems: 'end', alignContent: 'start', gap: '1rem' }}>
                  {vals.hasProbes ? (
                    <>
                      <div style={{ display: 'grid', gap: '0.5rem', justifyItems: 'end' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', letterSpacing: '0.14em', color: 'var(--nx-faint)' }}>
                          Probes
                        </span>
                        {(vals.probeList || []).map((probe, $index) => (
                          <React.Fragment key={$index}>
                            <span style={{ display: 'flex', alignItems: 'center', gap: '1rem', background: 'rgba(6,7,10,0.86)', padding: '0.4375rem 1.125rem', whiteSpace: 'nowrap' }}>
                              <span style={{ width: '1.25rem', height: '1.25rem', borderRadius: '50%', background: probe.tone }}></span>
                              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-ink)' }}>
                                {probe.name}
                              </span>
                              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-mute)' }}>
                                {probe.read}
                              </span>
                              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', color: probe.markTone }}>
                                {probe.away}
                              </span>
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {vals.geoBlocked ? (
                    <>
                      <div style={{ background: 'rgba(6,7,10,0.92)', borderLeft: '0.5rem solid var(--nx-accent)', padding: '1.125rem 1.75rem', display: 'grid', gap: '0.5rem', justifyItems: 'start' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.14em', color: 'var(--nx-accent)', whiteSpace: 'nowrap' }}>
                          ROAD GEOMETRY UNREACHABLE
                        </span>
                        <span style={{ fontSize: '1.875rem', lineHeight: '1.22', color: 'var(--nx-ink)', textWrap: 'pretty' }}>
                          Closed run, cross streets, jurisdiction and alternative are not drawn. This frame shows the operating area and live flow probes only.
                        </span>
                      </div>
                    </>
                  ) : null}
                </div>
                <div style={{ position: 'absolute', left: '3rem', right: '3rem', bottom: '2.25rem', display: 'grid', gap: '0.875rem', justifyItems: 'start' }}>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-faint)', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {vals.geoStatus}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '2.25rem', whiteSpace: 'nowrap', maxWidth: '100%', overflow: 'hidden' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', paddingRight: '2.25rem', borderRight: '0.0625rem solid rgba(255,255,255,0.16)' }}>
                      <span style={{ fontSize: '1.875rem', fontWeight: '700', letterSpacing: '0.16em', color: 'var(--nx-mute)' }}>
                        N
                      </span>
                      <span style={{ display: 'block', width: '12rem', height: '0.375rem', background: 'var(--nx-mute)' }}></span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                        {vals.mapScale}
                      </span>
                    </span>
                    {vals.layClosed ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '3rem', height: '0.625rem', background: 'var(--nx-accent)' }}></span>
                          Closed westbound
                        </span>
                      </>
                    ) : null}
                    {vals.layCross ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '1.5rem', height: '1.5rem', border: '0.3125rem solid var(--nx-accent)', transform: 'rotate(45deg)' }}></span>
                          Cross street on the closed run
                        </span>
                      </>
                    ) : null}
                    {vals.layDetour ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '3rem', height: '0.5rem', background: 'repeating-linear-gradient(to right, var(--nx-accent) 0 0.875rem, transparent 0.875rem 1.5rem)' }}></span>
                          Alternative · computed, not in record
                        </span>
                      </>
                    ) : null}
                    {vals.layState ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '3rem', height: '0.375rem', background: '#7C6BF0' }}></span>
                          ALDOT route
                        </span>
                      </>
                    ) : null}
                    {vals.layCity ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '3rem', height: '0.375rem', background: 'var(--nx-accent)' }}></span>
                          City street
                        </span>
                      </>
                    ) : null}
                    {vals.layTransit ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '3rem', height: '0.375rem', background: 'repeating-linear-gradient(to right, #4CC9F0 0 0.5rem, transparent 0.5rem 1rem)' }}></span>
                          Transit route
                        </span>
                      </>
                    ) : null}
                    {vals.layFlow ? (
                      <>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                          <span style={{ width: '1.75rem', height: '1.75rem', borderRadius: '50%', border: '0.25rem solid var(--nx-accent)' }}></span>
                          Flow probe
                        </span>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.isDelib ? (
          <>
            <div style={{ position: 'absolute', inset: '0', padding: '2.5rem 3rem', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', gap: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '2rem' }}>
                <span style={{ fontSize: '1.625rem', fontWeight: '700', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                  Deliberation · one evidence snapshot, six desks
                </span>
                <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', color: 'var(--nx-mute)' }}>
                  snapshot sha256 {vals.hashShort} · {vals.evidenceFrozen} · immutable
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 5rem 74rem', gap: '0', minHeight: '0' }}>
                <div style={{ display: 'grid', gridAutoRows: 'minmax(0, 1fr)', gap: '0.375rem', minHeight: '0' }}>
                  {(vals.desks || []).map(row => (
                  <button key={row.code} type="button" className="nxw-agent-row" aria-label={`Open ${row.name}`} onClick={vals[`open_${row.code}`]} style={{ display: 'grid', gridTemplateColumns: '12rem 15rem minmax(0, 1fr) minmax(0, 0.78fr) 18rem 14rem', columnGap: '2rem', alignItems: 'center', background: row.rowBg, border: '0', borderLeft: `0.375rem solid ${row.hue}`, padding: '1.25rem 1.5rem', overflow: 'hidden', cursor: 'pointer' }}>
                    <div style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.06em', color: row.nameColor, whiteSpace: 'nowrap' }}>
                      {row.name}
                    </div>
                    <div style={{ fontSize: '1.625rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {row.role}
                    </div>
                    <div style={{ fontSize: '1.875rem', color: row.lineColor, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {row.line}
                    </div>
                    <div style={{ fontSize: '1.625rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {row.note}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', whiteSpace: 'nowrap' }}>
                      <span style={{ width: '1.125rem', height: '1.125rem', borderRadius: '50%', flex: 'none', background: row.markFill || 'transparent', border: row.markBorder ? '0.1875rem solid ' + row.markBorder : '0' }}></span>
                      <span style={{ fontSize: '1.75rem', fontWeight: '600', color: row.statusColor }}>
                        {row.statusAt}
                      </span>
                    </div>
                    <div style={{ fontSize: '1.5rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontFamily: '\'JetBrains Mono\', monospace', textAlign: 'right' }}>
                      {row.meta}
                    </div>
                  </button>
                  ))}
                </div>
                <div style={{ position: 'relative' }}>
                  <div style={{ position: 'absolute', left: '50%', top: '6%', bottom: '6%', width: '0.125rem', background: 'rgba(255,255,255,0.18)' }}></div>
                  <div style={{ position: 'absolute', left: '50%', top: '50%', width: '2.5rem', height: '0.125rem', background: 'rgba(255,255,255,0.18)' }}></div>
                </div>
                <div style={{ background: 'rgba(232,119,34,0.06)', border: '0.0625rem solid rgba(232,119,34,0.35)', padding: '1.5rem 1.75rem', display: 'grid', alignContent: 'space-between', gap: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '1.25rem' }}>
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2.125rem', fontWeight: '700', letterSpacing: '0.08em', color: 'var(--nx-accent)' }}>
                      NEXUS
                    </span>
                    <span style={{ fontSize: '1.625rem', fontWeight: '700', letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                      Coordinator · composes, never authors
                    </span>
                  </div>
                  <div style={{ fontFamily: 'Archivo, sans-serif', fontStretch: '100%', fontSize: '2.875rem', fontWeight: '700', lineHeight: '1.1', letterSpacing: '-0.01em' }}>
                    {vals.composeLine}
                  </div>
                  <div style={{ height: '0.0625rem', background: 'rgba(255,255,255,0.12)' }}></div>
                  <div style={{ display: 'grid', gap: '0.5rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                      Playbook
                    </span>
                    <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', color: 'var(--nx-ink)' }}>
                      {vals.playbookLine}
                    </span>
                    <span style={{ fontSize: '1.75rem', color: 'var(--nx-mute)', lineHeight: '1.3' }}>
                      NEXUS may not author an action outside this playbook.
                    </span>
                  </div>
                  <div style={{ display: 'grid', gap: '0.5rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-accent)' }}>
                      Dissent carried forward
                    </span>
                    <span style={{ fontSize: '1.875rem', color: 'var(--nx-ink)', lineHeight: '1.25' }}>
                      {vals.dissentNote}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gap: '0.5rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                      Silence recorded
                    </span>
                    <span style={{ fontSize: '1.875rem', color: 'var(--nx-mute)', lineHeight: '1.25' }}>
                      {vals.silenceLine}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.isEvidence ? (
          <>
            <div className="nx-wall-evidence" style={{ position: 'absolute', inset: '0', padding: '0.75rem 3rem 1rem', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', gap: '0.5rem' }}>
              <div className="nx-wall-evidence__header" style={{ display: 'flex', alignItems: 'baseline', gap: '2rem' }}>
                <span style={{ fontSize: '2rem', fontWeight: '700', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                  Evidence lineage · flow of the record, left to right, append-only
                </span>
                <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                  {vals.linHint}
                </span>
                <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                  mode {vals.modeLive} · every edge is a citation, not an inference
                </span>
              </div>
              <div className="nx-wall-evidence__body" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(22rem, 28rem)', gap: '2rem', minHeight: '0', overflow: 'hidden' }}>
                <div className="nx-wall-lineage" ref={vals.linRef} style={{ position: 'relative', display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: '1.25rem', minHeight: '0', minWidth: '0', height: '100%', overflow: 'hidden' }}>
                  <svg width="100%" height="100%" style={{ position: 'absolute', inset: '0', zIndex: '0', pointerEvents: 'none', overflow: 'visible' }}>
                    <path d={vals.linDim} fill="rgba(154,161,171,0.20)" stroke="rgba(154,161,171,0.30)" strokeWidth="1"></path>
                    <path d={vals.linHot} fill="rgba(232,119,34,0.34)" stroke="rgba(232,119,34,0.85)" strokeWidth="1.5"></path>
                  </svg>
                  {(vals.lineageColumns || []).map(col => (
                  <div className="nx-wall-lineage__column" key={col.key} style={{ display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', gap: '0.5rem', minWidth: '0', minHeight: '0', height: '100%', overflow: 'hidden' }}>
                    <div className="nx-wall-lineage__stage" style={{ height: 'min-content', display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr) auto auto', alignItems: 'baseline', columnGap: '0.5rem', paddingBottom: '0.25rem', borderBottom: `0.1875rem solid ${col.headerTone}` }}>
                      <span className="nx-wall-lineage__number" style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                        {col.n}
                      </span>
                      <span className="nx-wall-lineage__label" style={{ fontSize: '2rem', fontWeight: '700', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        {col.label}
                      </span>
                      {col.subtitle ? (
                        <span className="nx-wall-lineage__subtitle" style={{ gridColumn: '1 / -1', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-mute)' }}>
                          {col.subtitle}
                        </span>
                      ) : null}
                      <span className="nx-wall-lineage__count" style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', color: 'var(--nx-ink)', fontVariantNumeric: 'tabular-nums' }}>
                        {col.cards.filter(card => card.id !== 'c-none' && card.id !== 'v-none' && card.id !== 'd-gate').length || (col.cards.length && col.cards[0].id !== 'c-none' && col.cards[0].id !== 'v-none' ? col.cards.length : 0)}
                      </span>
                      {col.key !== 'verification' ? (
                        <span className="nx-wall-lineage__arrow" style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', color: '#5A6270', marginLeft: '0.5rem' }}>
                          &#8594;
                        </span>
                      ) : null}
                    </div>
                    <div className="nx-wall-lineage__cards" style={{ display: 'grid', gridAutoRows: 'min-content', gap: '0.5rem', alignContent: 'start', minHeight: '0', overflow: 'auto' }}>
                    {col.cards.map(card => (
                      <button className="nx-wall-lineage__card" key={card.id} type="button" data-lin={card.id} onClick={vals[`sel_${card.id}`]} style={{ position: 'relative', zIndex: '1', textAlign: 'left', fontFamily: 'inherit', color: 'inherit', cursor: 'pointer', background: card.bg, border: '0', borderLeft: `0.375rem solid ${vals[`bd_${card.id}`] || card.tone}`, padding: '0.5rem 0.75rem', display: 'grid', alignContent: 'start', gap: '0.125rem', minHeight: '0', minWidth: '0', width: '100%', overflow: 'hidden' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', lineHeight: '1.12', letterSpacing: '0.06em', color: card.kickerTone, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {card.kicker}
                        </span>
                        <span style={{ fontSize: '1.75rem', lineHeight: '1.14', color: 'var(--nx-ink)', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                          {card.title}
                        </span>
                        {card.meta ? (
                          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', color: 'var(--nx-faint)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {card.meta}
                          </span>
                        ) : null}
                      </button>
                    ))}
                    </div>
                  </div>
                  ))}
                </div>
                <div className="nx-wall-lineage__inspector" style={{ background: 'var(--nx-surface)', borderLeft: '0.375rem solid #2A3038', padding: '1.25rem 1.5rem', display: 'grid', gridAutoRows: 'min-content', gap: '0.875rem', minHeight: '0', overflow: 'hidden' }}>
                  <span style={{ fontSize: '2rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                    Inspector
                  </span>
                  {vals.linEmpty ? (
                    <>
                      <span style={{ fontSize: '1.875rem', color: 'var(--nx-mute)', lineHeight: '1.28' }}>
                        Select any record to trace what it was built from and what it feeds. The lit path is the citation chain — nothing else is implied.
                      </span>
                    </>
                  ) : null}
                  {vals.linPicked ? (
                    <>
                      <div style={{ display: 'grid', gap: '0.875rem' }}>
                        <div style={{ display: 'grid', gap: '0.25rem' }}>
                          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', letterSpacing: '0.08em', color: vals.linTone }}>
                            {vals.linStage}
                          </span>
                          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2.125rem', fontWeight: '700', color: 'var(--nx-ink)', lineHeight: '1.1', wordBreak: 'break-all' }}>
                            {vals.linId}
                          </span>
                        </div>
                        <div style={{ height: '0.0625rem', background: 'rgba(255,255,255,0.12)' }}></div>
                        <div style={{ display: 'grid', gridTemplateColumns: '11rem minmax(0, 1fr)', gap: '0.375rem 1rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem' }}>
                          <span style={{ color: 'var(--nx-mute)' }}>
                            source
                          </span>
                          <span style={{ color: 'var(--nx-ink)' }}>
                            {vals.linSrc}
                          </span>
                          <span style={{ color: 'var(--nx-mute)' }}>
                            recorded
                          </span>
                          <span style={{ color: 'var(--nx-ink)' }}>
                            {vals.linAt}
                          </span>
                          <span style={{ color: 'var(--nx-mute)' }}>
                            built from
                          </span>
                          <span style={{ color: 'var(--nx-ink)' }}>
                            {vals.linUp}
                          </span>
                          <span style={{ color: 'var(--nx-mute)' }}>
                            feeds
                          </span>
                          <span style={{ color: 'var(--nx-ink)' }}>
                            {vals.linDown}
                          </span>
                        </div>
                        <div style={{ height: '0.0625rem', background: 'rgba(255,255,255,0.12)' }}></div>
                        <span style={{ fontSize: '1.75rem', color: 'var(--nx-ink)', lineHeight: '1.26', textWrap: 'pretty' }}>
                          {vals.linNote}
                        </span>
                      </div>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.isDecision ? (
          <>
            <div style={{ position: 'absolute', inset: '0', padding: '1.5rem 3rem 1.75rem', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', gap: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '2rem' }}>
                <span style={{ fontSize: '1.625rem', fontWeight: '700', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                  The decision · the wall witnesses, the desk signs
                </span>
                <span style={{ marginLeft: 'auto', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', color: 'var(--nx-accent)' }}>
                  {vals.recExpiresRemaining}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 72rem 76rem', gap: '2rem' }}>
                <div style={{ display: 'grid', alignContent: 'space-between', gap: '0.875rem' }}>
                  <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                    What is being decided
                  </span>
                  <div style={{ fontSize: '2.625rem', fontWeight: '600', lineHeight: '1.14', textWrap: 'pretty' }}>
                    {vals.recAction}
                  </div>
                  <div style={{ height: '0.0625rem', background: 'rgba(255,255,255,0.12)' }}></div>
                  <div style={{ display: 'grid', gap: '0.375rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                      Expected effect
                    </span>
                    <span style={{ fontSize: '1.875rem', color: 'var(--nx-ink)', lineHeight: '1.24' }}>
                      {vals.expectedEffect}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gap: '0.375rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-accent)' }}>
                      Stated limitations
                    </span>
                    <span style={{ fontSize: '1.875rem', color: 'var(--nx-mute)', lineHeight: '1.24' }}>
                      {vals.limitations}
                    </span>
                  </div>
                </div>
                <div style={{ background: 'var(--nx-surface)', padding: '1.25rem 1.75rem', display: 'grid', alignContent: 'space-between', gap: '0.875rem' }}>
                  <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                    Who must agree
                  </span>
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '0.75rem 1.5rem', alignItems: 'center' }}>
                    {(vals.approvals || []).map(party => (
                      <React.Fragment key={party.id}>
                        <div>
                          <div style={{ fontSize: '2.125rem', fontWeight: '600' }}>
                            {party.agency}
                          </div>
                          <div style={{ fontSize: '1.625rem', color: 'var(--nx-mute)' }}>
                            {party.role}
                          </div>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                          <span style={{ width: '1.125rem', height: '1.125rem', borderRadius: '50%', background: party.fill }}></span>
                          <span style={{ fontSize: '1.75rem', fontWeight: '600', color: party.statusColor }}>
                            {party.status}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ height: '0.0625rem', background: 'rgba(255,255,255,0.12)' }}></div>
                  <div style={{ display: 'grid', gap: '0.5rem' }}>
                    <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                      What approval does
                    </span>
                    <span style={{ fontSize: '1.75rem', color: 'var(--nx-ink)', lineHeight: '1.24' }}>
                      Creates accountable agency commitments. It does not change a signal, close a road, or dispatch a crew.
                    </span>
                  </div>
                </div>
                <div style={{ background: 'rgba(232,119,34,0.07)', border: '0.0625rem solid rgba(232,119,34,0.4)', padding: '1.25rem 1.75rem', display: 'grid', alignContent: 'space-between', gap: '0.875rem' }}>
                  <span style={{ fontSize: '1.5rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-accent)' }}>
                    How it will be signed
                  </span>
                  <div>
                    <div style={{ fontFamily: 'Archivo, sans-serif', fontStretch: '100%', fontSize: '3.25rem', fontWeight: '700', lineHeight: '1.05' }}>
                      {vals.operatorName}
                    </div>
                    <div style={{ fontSize: '1.875rem', color: 'var(--nx-mute)', marginTop: '0.375rem' }}>
                      {vals.operatorRole}
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '20rem minmax(0, 1fr)', gap: '0.625rem 1.5rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem' }}>
                    <span style={{ color: 'var(--nx-mute)' }}>
                      expected version
                    </span>
                    <span>
                      {vals.recVersion}
                    </span>
                    <span style={{ color: 'var(--nx-mute)' }}>
                      expected state
                    </span>
                    <span>
                      {vals.recState}
                    </span>
                    <span style={{ color: 'var(--nx-mute)' }}>
                      snapshot sha256
                    </span>
                    <span>
                      {vals.hashShort}
                    </span>
                    <span style={{ color: 'var(--nx-mute)' }}>
                      decided at
                    </span>
                    <span>
                      {vals.decidedAt}
                    </span>
                  </div>
                  <div style={{ height: '0.0625rem', background: 'rgba(232,119,34,0.25)' }}></div>
                  <div style={{ fontSize: '1.75rem', color: 'var(--nx-ink)', lineHeight: '1.24' }}>
                    The wall cannot sign. It records who did, when, against which frozen evidence — and refuses the write if any of it has moved.
                  </div>
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.isCommit ? (
          <>
            <div style={{ position: 'absolute', inset: '0', padding: '2.5rem 3rem', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', gap: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '2rem' }}>
                <span style={{ fontSize: '1.625rem', fontWeight: '700', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                  {vals.commitmentsHeader}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: 'var(--nx-accent)' }}>
                    {vals.commitmentsExecuting}
                  </span>
                  {' '}in progress ·{' '}
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: 'var(--nx-ink)' }}>
                    {vals.commitmentsAcceptedCount}
                  </span>
                  {' '}accepted ·{' '}
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', color: 'var(--nx-accent)' }}>
                    {vals.blockedCount}
                  </span>
                  {' '}blocked
                </span>
              </div>
              <div style={{ display: 'grid', gridAutoRows: 'minmax(0, 1fr)', gap: '1rem' }}>
                {vals.noCommitments ? (
                  <div style={{ background: 'var(--nx-surface)', borderLeft: '0.375rem solid var(--nx-mute)', padding: '1.75rem', fontSize: '2.125rem', color: 'var(--nx-mute)', lineHeight: '1.3' }}>
                    None yet. They appear after a named person signs, not before.
                  </div>
                ) : null}
                {(vals.commitmentPreview || []).map(row => (
                  <div key={row.id} style={{ background: 'var(--nx-surface)', borderLeft: `0.375rem solid ${row.border}`, padding: '1.75rem', display: 'grid', gridTemplateColumns: '34rem minmax(0, 1fr) 84rem', columnGap: '2.5rem', alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: '2.25rem', fontWeight: '600' }}>
                        {row.agency}
                      </div>
                      <div style={{ fontSize: '1.625rem', color: 'var(--nx-mute)', marginTop: '0.25rem' }}>
                        Owner {row.owner} · {row.due}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: '2.125rem', lineHeight: '1.22' }}>
                        {row.outcome}
                      </div>
                      <div style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', color: 'var(--nx-mute)', marginTop: '0.375rem' }}>
                        {row.note}
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0.5rem' }}>
                      {(row.stages || []).map(stage => (
                        <div key={stage.key} style={{ borderTop: `0.25rem solid ${stage.color}`, paddingTop: '0.625rem', fontSize: '1.5rem', fontWeight: stage.weight, color: stage.color }}>
                          {stage.label}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : null}
        {vals.isWorkflow ? (
          <WorkflowBoard feeds={vals.feeds} stakeholder={vals.operatorName} />
        ) : null}
      </main>
      <nav className="nx-wall-nav" data-screen-label="Stratum 3 — reach band, screens" style={{ background: '#12151B', borderTop: '0.1875rem solid rgba(255,255,255,0.18)', padding: '0.75rem 2rem', display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '1rem' }}>
        <button type="button" className="nxw-tab" onClick={vals.goOps} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeOps}`, color: vals.inkOps, fontFamily: 'inherit', textAlign: 'left', padding: '0 2.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            01
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            Operations
          </span>
        </button>
        <button type="button" className="nxw-tab" onClick={vals.goDelib} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeDelib}`, color: vals.inkDelib, fontFamily: 'inherit', textAlign: 'left', padding: '0 2.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            02
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            Deliberation
          </span>
        </button>
        <button type="button" className="nxw-tab" onClick={vals.goEvidence} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeEvidence}`, color: vals.inkEvidence, fontFamily: 'inherit', textAlign: 'left', padding: '0 2.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            03
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            Evidence lineage
          </span>
        </button>
        <button type="button" className="nxw-tab" onClick={vals.goDecision} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeDecision}`, color: vals.inkDecision, fontFamily: 'inherit', textAlign: 'left', padding: '0 2.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            04
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            The decision
          </span>
        </button>
        <button type="button" className="nxw-tab" onClick={vals.goCommit} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeCommit}`, color: vals.inkCommit, fontFamily: 'inherit', textAlign: 'left', padding: '0 2.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            05
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            Commitments
          </span>
        </button>
        <button type="button" className="nxw-tab" onClick={vals.goWorkflow} style={{ height: '7rem', background: 'var(--nx-raised)', border: '0', borderLeft: `0.5rem solid ${vals.edgeWorkflow}`, color: vals.inkWorkflow, fontFamily: 'inherit', textAlign: 'left', padding: '0 1.5rem', display: 'flex', alignItems: 'center', gap: '1rem', cursor: 'pointer' }}>
          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.5rem', letterSpacing: '0.12em', opacity: '0.6' }}>
            06
          </span>
          <span style={{ fontSize: '2.5rem', fontWeight: '600' }}>
            Workflow
          </span>
        </button>
      </nav>
      {vals.showDesks ? (
        <>
          <section className="nx-wall-desks" data-screen-label="Desks panel" style={{ borderTop: '0.1875rem solid rgba(255,255,255,0.18)', borderBottom: '0.1875rem solid rgba(255,255,255,0.18)', display: 'grid', gridTemplateRows: '4.5rem minmax(0, 1fr)', background: '#08090C' }}>
            <div className="nx-wall-desks__head" style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', padding: '0 1.5rem', borderBottom: '0.1875rem solid rgba(255,255,255,0.18)', background: '#0E1116' }}>
              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', fontWeight: '700', letterSpacing: '0.18em' }}>
                Desks
              </span>
              <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-mute)' }}>
                {vals.deskStrip}
              </span>
              <span style={{ marginLeft: 'auto', fontSize: '1.5rem', color: 'var(--nx-faint)' }}>
                {vals.snapshotLine}
              </span>
            </div>
            <div className="nx-wall-desks__grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: '1.25rem', padding: '1rem 1.5rem', minHeight: '0', overflow: 'hidden' }}>
              {(vals.wallDesks || []).map(tile => (
              <button key={tile.code} type="button" className="nxw-desk" data-desk={tile.code} aria-label={`Open ${tile.name}`} onClick={vals[`open_${tile.code}`]} style={{ background: vals[`bg_${tile.code}`], border: '0.0625rem solid rgba(255,255,255,0.12)', borderTop: `0.375rem solid ${vals[`gut_${tile.code}`]}`, padding: '1.125rem 1.375rem', display: 'grid', gridTemplateRows: 'auto auto minmax(0, 1fr)', gap: '0.625rem', alignContent: 'space-between', minWidth: '0', cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', minWidth: '0' }}>
                  <span style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <img src={tile.avatar} alt="" style={{ width: '4.25rem', height: '4.25rem', objectFit: 'cover', display: 'block', pointerEvents: 'none' }} />
                    <img src={tile.logo} alt="" style={{ width: '4.25rem', height: '4.25rem', display: 'block', pointerEvents: 'none' }} />
                  </span>
                  <span style={{ display: 'grid', gap: '0.125rem', minWidth: '0' }}>
                    <span style={{ fontFamily: 'Archivo, sans-serif', fontSize: '2.125rem', fontWeight: '700', letterSpacing: '0.02em', color: 'var(--nx-ink)', whiteSpace: 'nowrap' }}>
                      {tile.name}
                    </span>
                    <span style={{ fontSize: '1.625rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {tile.role}
                    </span>
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', whiteSpace: 'nowrap', minWidth: '0' }}>
                  <span style={{ width: '1.25rem', height: '1.25rem', flex: 'none', background: tile.markFill || 'transparent', border: tile.markBorder ? `0.1875rem solid ${tile.markBorder}` : '0' }}></span>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', fontWeight: '700', letterSpacing: '0.1em', color: tile.statusColor }}>
                    {tile.status}
                  </span>
                </div>
                <div style={{ display: 'grid', gap: '0.5rem', minWidth: '0' }}>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.75rem', color: 'var(--nx-mute)', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                    {tile.meta}
                  </span>
                  <span className="nxw-desk__open">
                    Open
                  </span>
                </div>
              </button>
              ))}
            </div>
          </section>
        </>
      ) : null}
      {vals.isOps ? (
        <>
          <div className="nx-wall-record-slot" style={{ position: 'relative', minHeight: '0', overflow: 'hidden' }}>
            {vals.deskClosed ? (
              <>
                <div style={{ height: '100%' }}>
                  <footer className="nx-wall-record" data-screen-label="Stratum 6 — the record" style={{ padding: '1rem 3rem 0.5rem', display: 'grid', gridTemplateRows: 'auto auto', gap: '1rem', background: 'var(--nx-ground)', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: '2rem' }}>
                      <span style={{ fontSize: '2rem', fontWeight: '700', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        The record · last 90 minutes
                      </span>
                      <span style={{ display: 'flex', alignItems: 'baseline', gap: '2rem', marginLeft: 'auto', whiteSpace: 'nowrap' }}>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)' }}>
                          {vals.detectedLine}
                        </span>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)' }}>
                          {vals.recAuthoredLine}
                        </span>
                      </span>
                    </div>
                    <div style={{ position: 'relative', display: 'grid', gridTemplateColumns: '15rem minmax(0, 1fr)', columnGap: '2rem', rowGap: '0.25rem', alignContent: 'start' }}>
                      <div style={{ position: 'absolute', left: '17rem', right: '0', top: '3.25rem', bottom: '2.25rem', pointerEvents: 'none' }}>
                        {vals.record?.detectedPct ? (
                          <div style={{ position: 'absolute', left: vals.record.detectedPct, top: '0', bottom: '0', width: '0.125rem', background: 'rgba(232,119,34,0.55)' }}></div>
                        ) : null}
                        {vals.record?.recPct ? (
                          <div style={{ position: 'absolute', left: vals.record.recPct, top: '0', bottom: '0', width: '0.125rem', background: 'rgba(232,119,34,0.6)' }}></div>
                        ) : null}
                      </div>
                      <span></span>
                      <span style={{ display: 'flex', justifyContent: 'space-between', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                        {(vals.record?.ticks || []).map((tick, i) => (
                          <span key={tick + i} style={{ color: i === (vals.record.ticks.length - 1) ? 'var(--nx-ink)' : 'var(--nx-mute)' }}>{tick}</span>
                        ))}
                      </span>
                      {(vals.record?.lanes || []).map(lane => (
                        <React.Fragment key={lane.code}>
                          <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', color: lane.nameColor, alignSelf: 'center' }}>
                            {lane.name}
                          </span>
                          <div style={{ position: 'relative', height: '1.75rem', background: '#0D1015', borderLeft: `0.1875rem solid ${lane.hue}` }}>
                            {lane.marks.map((mark, i) => (
                              mark.hollow ? (
                                <div key={i} style={{ position: 'absolute', left: mark.left, top: '0.25rem', width: '1.5rem', height: '1.5rem', border: `0.1875rem solid ${mark.color}` }}></div>
                              ) : (
                                <div key={i} style={{ position: 'absolute', left: mark.left, top: '0.375rem', width: '1.5%', height: '1.25rem', background: mark.color }}></div>
                              )
                            ))}
                          </div>
                        </React.Fragment>
                      ))}
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', color: 'var(--nx-mute)', alignSelf: 'center' }}>
                        EVIDENCE
                      </span>
                      <div style={{ position: 'relative', height: '1.75rem', background: '#0D1015', borderLeft: '0.1875rem solid var(--nx-accent)' }}>
                        {(vals.record?.evidenceMarks || []).map((mark, i) => (
                          <div key={i} style={{ position: 'absolute', left: mark.left, top: '0.375rem', width: '1.5%', height: '1.25rem', background: mark.color }}></div>
                        ))}
                      </div>
                    </div>
                  </footer>
                </div>
              </>
            ) : null}
          </div>
        </>
      ) : null}
      {vals.deskOpen ? (
        <>
          <div style={{ position: 'absolute', inset: '0', zIndex: '50', background: 'rgba(3,4,6,0.88)', display: 'grid', placeItems: 'center', padding: '4rem' }}>
            <div className="nx-wall-config" data-screen-label="Desk configuration" style={{ width: '100%', height: '100%', background: '#0E1116', border: '0.0625rem solid rgba(255,255,255,0.22)', display: 'grid', gridTemplateRows: 'auto auto minmax(0, 1fr) auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '2rem', padding: '1.75rem 2.5rem', borderBottom: '0.1875rem solid rgba(255,255,255,0.18)', background: '#12161C' }}>
                <span style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <img src={vals.deskAvatar} alt="" style={{ width: '7rem', height: '7rem', objectFit: 'cover', display: 'block' }} />
                  <img src={vals.deskLogo} alt="" style={{ width: '7rem', height: '7rem', display: 'block' }} />
                </span>
                <span style={{ display: 'grid', gap: '0.25rem', minWidth: '0' }}>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '3rem', fontWeight: '700', letterSpacing: '0.08em', color: 'var(--nx-ink)' }}>
                    DESK {vals.deskCode}
                  </span>
                  <span style={{ fontSize: '2rem', color: 'var(--nx-mute)' }}>
                    {vals.deskSteward} · {vals.deskMission}
                  </span>
                </span>
                <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
                  <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: vals.dirtyTone }}>
                    {vals.dirtyLabel}
                  </span>
                  <button className="nxw-h9" type="button" onClick={vals.restoreDesk} style={{ padding: '0.875rem 2rem', background: 'transparent', border: '0.0625rem solid rgba(255,255,255,0.22)', color: 'var(--nx-mute)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.1em', cursor: 'pointer' }}>
                    RESTORE
                  </button>
                  <button className="nxw-h10" type="button" onClick={vals.closeDesk} style={{ padding: '0.875rem 2rem', background: 'transparent', border: '0.0625rem solid rgba(255,255,255,0.22)', color: 'var(--nx-mute)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.1em', cursor: 'pointer' }}>
                    CANCEL
                  </button>
                  <button type="button" onClick={vals.saveDesk} style={{ padding: '0.875rem 2.5rem', background: 'var(--nx-accent)', border: '0', color: 'var(--nx-ground)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.1em', cursor: 'pointer' }}>
                    SAVE
                  </button>
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: '0.1875rem solid rgba(255,255,255,0.18)' }}>
                <button type="button" onClick={vals.goIdentity} style={{ padding: '1.25rem 3rem', background: 'transparent', border: '0', borderBottom: `0.3125rem solid ${vals.edgeIdentity}`, color: vals.inkIdentity, fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.14em', cursor: 'pointer' }}>
                  ROLE
                </button>
                <button type="button" onClick={vals.goPrompt} style={{ padding: '1.25rem 3rem', background: 'transparent', border: '0', borderBottom: `0.3125rem solid ${vals.edgePrompt}`, color: vals.inkPrompt, fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.14em', cursor: 'pointer' }}>
                  PROMPT
                </button>
                <button type="button" onClick={vals.goModel} style={{ padding: '1.25rem 3rem', background: 'transparent', border: '0', borderBottom: `0.3125rem solid ${vals.edgeModel}`, color: vals.inkModel, fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.14em', cursor: 'pointer' }}>
                  MODEL
                </button>
                <button type="button" onClick={vals.goTools} style={{ padding: '1.25rem 3rem', background: 'transparent', border: '0', borderBottom: `0.3125rem solid ${vals.edgeTools}`, color: vals.inkTools, fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.14em', cursor: 'pointer' }}>
                  TOOLS
                </button>
                <button type="button" onClick={vals.goPolicies} style={{ padding: '1.25rem 3rem', background: 'transparent', border: '0', borderBottom: `0.3125rem solid ${vals.edgePolicies}`, color: vals.inkPolicies, fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', letterSpacing: '0.14em', cursor: 'pointer' }}>
                  POLICIES
                </button>
              </div>
              <div style={{ minHeight: '0', overflow: 'auto', padding: '2.5rem' }}>
                {vals.tabIdentity ? (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '26rem minmax(0, 1fr)', gap: '1.75rem 3rem', alignContent: 'start' }}>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)', paddingTop: '0.5rem' }}>
                        Role
                      </span>
                      <textarea rows="3" value={vals.edRole} onChange={vals.onRole} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: 'inherit', fontSize: '2rem', lineHeight: '1.3', padding: '0.875rem 1.125rem', resize: 'none' }}></textarea>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)', paddingTop: '0.5rem' }}>
                        Backstory
                      </span>
                      <textarea rows="4" value={vals.edBackstory} onChange={vals.onBackstory} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: 'inherit', fontSize: '2rem', lineHeight: '1.3', padding: '0.875rem 1.125rem', resize: 'none' }}></textarea>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Allowed connectors
                      </span>
                      <input type="text" value={vals.edConnectors} onChange={vals.onConnectors} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', padding: '0.75rem 1.125rem' }} />
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Constraint
                      </span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)', lineHeight: '1.3', borderLeft: '0.25rem solid var(--nx-accent)', paddingLeft: '1.25rem' }}>
                        {vals.deskBoundary}
                      </span>
                    </div>
                  </>
                ) : null}
                {vals.tabPrompt ? (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '26rem minmax(0, 1fr)', gap: '1.75rem 3rem', alignContent: 'start' }}>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)', paddingTop: '0.5rem' }}>
                        Prompt
                      </span>
                      <textarea rows="8" value={vals.edPrompt} onChange={vals.onPrompt} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: 'inherit', fontSize: '2rem', lineHeight: '1.3', padding: '0.875rem 1.125rem', resize: 'none' }}></textarea>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Appended
                      </span>
                      <span style={{ fontSize: '2rem', color: 'var(--nx-mute)', lineHeight: '1.3' }}>
                        Locked rules about permitted feeds and field control are appended after this prompt on every run and cannot be edited here.
                      </span>
                    </div>
                  </>
                ) : null}
                {vals.showModel ? (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '26rem minmax(0, 1fr)', gap: '1.75rem 3rem', alignContent: 'start' }}>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)', paddingTop: '0.5rem' }}>
                        Model
                      </span>
                      <span style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                        {(vals.modelOptions || []).map((opt, $index) => (
                          <React.Fragment key={$index}>
                            <button type="button" onClick={opt.pick} style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', padding: '0.625rem 1.5rem', background: opt.bg, border: `0.125rem solid ${opt.border}`, color: opt.ink, cursor: 'pointer' }}>
                              {opt.name}
                            </button>
                          </React.Fragment>
                        ))}
                      </span>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Temperature
                      </span>
                      <input type="text" value={vals.edTemp} onChange={vals.onTemp} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', padding: '0.75rem 1.125rem' }} />
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Max turns
                      </span>
                      <input type="text" value={vals.edTurns} onChange={vals.onTurns} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', padding: '0.75rem 1.125rem' }} />
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Timeout (ms)
                      </span>
                      <input type="text" value={vals.edTimeout} onChange={vals.onTimeout} spellcheck="false" style={{ width: '100%', background: 'var(--nx-surface)', border: '0.0625rem solid rgba(255,255,255,0.20)', color: 'var(--nx-ink)', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', padding: '0.75rem 1.125rem' }} />
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Runtime
                      </span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-mute)' }}>
                        host groq · key set on the server, never returned to the wall
                      </span>
                    </div>
                  </>
                ) : null}
                {vals.showTools ? (
                  <>
                    <div style={{ display: 'grid', gap: '0.5rem', alignContent: 'start' }}>
                      {(vals.toolRows || []).map((tool, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ display: 'grid', gridTemplateColumns: '9rem 8rem 34rem minmax(0, 1fr)', gap: '2rem', alignItems: 'center', padding: '0.75rem 0', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
                            <button type="button" onClick={tool.toggle} style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', fontWeight: '700', padding: '0.375rem 0', background: tool.bg, border: `0.125rem solid ${tool.border}`, color: tool.ink, cursor: 'pointer' }}>
                              {tool.state}
                            </button>
                            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)' }}>
                              {tool.req}
                            </span>
                            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-ink)' }}>
                              {tool.name}
                            </span>
                            <span style={{ fontSize: '2rem', color: '#B6BAC1', lineHeight: '1.28' }}>
                              {tool.desc}
                            </span>
                          </div>
                        </React.Fragment>
                      ))}
                      <div style={{ display: 'grid', gridTemplateColumns: '34rem minmax(0, 1fr)', gap: '2rem', paddingTop: '1.25rem' }}>
                        <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                          Locked action families
                        </span>
                        <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)' }}>
                          {vals.deskFamilies}
                        </span>
                      </div>
                    </div>
                  </>
                ) : null}
                {vals.showPolicies ? (
                  <>
                    <div style={{ display: 'grid', gap: '0.5rem', alignContent: 'start' }}>
                      {(vals.deskPolicies || []).map((policy, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ display: 'grid', gridTemplateColumns: '38rem 16rem minmax(0, 1fr)', gap: '2.5rem', alignItems: 'baseline', padding: '0.875rem 0', borderBottom: '0.0625rem solid rgba(255,255,255,0.10)' }}>
                            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-ink)' }}>
                              {policy.id}
                            </span>
                            <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)' }}>
                              {policy.jurisdiction}
                            </span>
                            <span style={{ fontSize: '2rem', color: '#B6BAC1', lineHeight: '1.28' }}>
                              {policy.title}
                            </span>
                          </div>
                        </React.Fragment>
                      ))}
                      <div style={{ paddingTop: '1.25rem', fontSize: '2rem', color: 'var(--nx-mute)', lineHeight: '1.28' }}>
                        Policy notes are reference. A note is never evidence and never opens an incident.
                      </div>
                    </div>
                  </>
                ) : null}
                {vals.showRuleNotice ? (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '26rem minmax(0, 1fr)', gap: '1.75rem 3rem', alignContent: 'start' }}>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Assessor
                      </span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2.125rem', color: 'var(--nx-ink)' }}>
                        deterministic rule assessor · no model
                      </span>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Editable
                      </span>
                      <span style={{ fontSize: '2rem', color: '#B6BAC1', lineHeight: '1.3' }}>
                        Model, tools and policy notes become editable once a desk-level agent loop is enabled for {vals.deskCode} on the server. Role and prompt describe what the assessor evaluates today.
                      </span>
                      <span style={{ fontSize: '1.75rem', fontWeight: '700', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--nx-mute)' }}>
                        Constraint
                      </span>
                      <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '2rem', color: 'var(--nx-accent)', lineHeight: '1.3', borderLeft: '0.25rem solid var(--nx-accent)', paddingLeft: '1.25rem' }}>
                        {vals.deskBoundary}
                      </span>
                    </div>
                  </>
                ) : null}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', padding: '1.25rem 2.5rem', borderTop: '0.1875rem solid rgba(255,255,255,0.18)', background: 'var(--nx-surface)' }}>
                <span style={{ fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.875rem', color: 'var(--nx-mute)' }}>
                  Edits apply to the next run of this desk. They never alter a frozen snapshot or a signed decision.
                </span>
              </div>
            </div>
          </div>
        </>
      ) : null}
      {vals.reachOverlay ? (
        <>
          <div style={{ position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '30' }}>
            <div style={{ position: 'absolute', left: '0', right: '0', top: '62.5rem', borderTop: '0.1875rem dashed #5B9CF5' }}></div>
            <div style={{ position: 'absolute', right: '3rem', top: '59.5rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', color: '#5B9CF5' }}>
              y 1000 · comfortable reach begins
            </div>
            <div style={{ position: 'absolute', left: '0', right: '0', top: '100rem', borderTop: '0.1875rem dashed #5B9CF5' }}></div>
            <div style={{ position: 'absolute', right: '3rem', top: '100.75rem', fontFamily: '\'JetBrains Mono\', monospace', fontSize: '1.625rem', color: '#5B9CF5' }}>
              y 1600 · below knee height on a floor-mounted panel — display only
            </div>
            <div style={{ position: 'absolute', left: '0', right: '0', top: '62.5rem', height: '37.5rem', background: 'rgba(91,156,245,0.07)' }}></div>
          </div>
        </>
      ) : null}
    </div>
    </>
  );
}
