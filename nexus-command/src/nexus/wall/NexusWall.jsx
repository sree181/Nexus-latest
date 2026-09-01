/**
 * CIVIC INSTRUMENT PANEL
 * Preserve live operational behavior while the presentation scales from desktop review to a 4K command wall.
 */
import React from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import NexusWallTemplate from './NexusWallTemplate.jsx';
import './nexusWall.css';
import { operationalApi } from '../../operationalApi';
import { getLive, subscribeLive } from '../liveStore';
import { buildLiveView } from '../liveView';

window.L = L;

/* Ported from Nexus Wall.dc.html. Behaviour, timers, map wiring, data and copy are unchanged.
   renderVals() is the only bridge between logic and markup. */

const SCREENS = ['operations', 'deliberation', 'evidence', 'decision', 'commitments', 'workflow'];

/* Desk copy, boundaries, roles and prompts lifted from server/operational/agents/*
   (desks.ts boundaries, atlas/catalog.ts and aqua/catalog.ts defaults, DeskConfigDialog copy). */
const DESK_PROFILES = {
  atlas: {
    mission: 'Keep arrival and egress corridors moving while avoiding spillback into campus and neighbourhood streets.',
    connectors: 'aldot-algo-traffic-v1 · tomtom-traffic-flow-v1 · aldot-traffic-counts-v1',
    name: 'ATLAS', kicker: 'Traffic desk', configurable: true,
    boundary: 'ATLAS cannot change a signal plan, close a road, or publish traffic-control instructions.',
    role: 'Traffic operations desk for Auburn arrival and egress corridors.',
    backstory: 'ATLAS is the traffic reviewer on Nexus Coordinate. It watches ALDOT traveler events, I-85 travel times, licensed probe speed, and reference counts in the Auburn box. It is used to game-day spillback and weekday restrictions, and it knows City Traffic Engineering owns signal timing. It does not sit in a cabinet, does not dispatch, and does not speak for parking, transit, weather, or emergency access.',
    instructions: 'Read the permitted feeds before you write. If a policy note bears on the corridor, say so in the interpretation and still cite only evidence ids. Stay quiet when nothing in the feeds bears on this incident.',
  },
  aqua: {
    mission: 'Balance parking, remote-lot, curb, and shuttle demand.',
    connectors: 'auburn-eta-spot-v1 · auburn-parking-occupancy-v1',
    name: 'AQUA', kicker: 'Parking and transit desk', configurable: true,
    boundary: 'AQUA cannot change an operator schedule or parking policy without agency authorization.',
    role: 'Parking and transit desk for Auburn remote lots, curb, and Tiger Transit staging.',
    backstory: 'AQUA is the parking and transit reviewer on Nexus Coordinate. It watches Tiger Transit vehicle positions and, when a partner feed exists, lot occupancy. It knows Parking & Transit owns schedules and lot policy, and that occupancy is often not connected. It does not speak for corridor speed, weather, or emergency access.',
    instructions: 'Read the permitted feeds before you write. If lot occupancy is not connected, say so plainly. Do not infer a full lot from shuttle delay. If a policy note bears on staging, say so in the interpretation and still cite only evidence ids.',
  },
  sentinel: {
    mission: 'Protect pedestrian movement and public-safety access near campus and the stadium.',
    connectors: 'nws-weather-alerts-v1 · auburn-emergency-access-v1',
    name: 'SENTINEL', kicker: 'Public safety desk', configurable: false,
    boundary: 'SENTINEL cannot dispatch police, issue a public-safety order, or send a public alert.',
    role: 'Public safety and operational-technology desk for the Auburn operating box.',
    backstory: 'SENTINEL reviews security and systems alerts inside the operating box and reports whether anything bears on the incident under review.',
    instructions: 'Report only what a permitted security or systems alert says. Abstain when nothing in the window bears on the incident.',
  },
  phoenix: {
    mission: 'Maintain a viable emergency-response corridor.',
    connectors: 'auburn-emergency-access-v1 · coa-road-closures-v1',
    name: 'PHOENIX', kicker: 'Emergency routes desk', configurable: false,
    boundary: 'PHOENIX cannot dispatch apparatus, alter clinical decisions, or override incident command.',
    role: 'Emergency access desk for hospital and fire response routes in the operating box.',
    backstory: 'PHOENIX reviews restricted corridor status and emergency-access state, and says when a proposed action would lengthen or constrain a response route.',
    instructions: 'State the effect on the response route in plain words. Cite evidence ids. Dissent when the playbook action would constrain emergency access.',
  },
  forge: {
    mission: 'Identify mobility risks caused by infrastructure conditions.',
    connectors: 'coa-road-closures-v1 · usgs-natural-hazards-v1',
    name: 'FORGE', kicker: 'Roads and utilities desk', configurable: false,
    boundary: 'FORGE cannot operate a pump, utility, traffic cabinet, or any other field device.',
    role: 'Roads and utilities desk for published closures, detours and public-works restrictions.',
    backstory: 'FORGE reads the City of Auburn published closure record and reports the authoritative restriction extent. It is the only desk that reads the closure connector.',
    instructions: 'Report the published restriction exactly as recorded. Never infer a closure from delay.',
  },
  echo: {
    mission: 'Maintain reliable communications and prepare verified public-facing language.',
    connectors: 'aldot-algo-traffic-v1 · nexus-siem-alerts-v1',
    name: 'ECHO', kicker: 'Communications desk', configurable: false,
    boundary: 'ECHO cannot send a public alert, issue a media statement, or isolate a network.',
    role: 'Communications desk for traveller-facing wording and agency notification drafts.',
    backstory: 'ECHO drafts wording for agency review. Publication to a message sign or a public channel is always a manual agency action.',
    instructions: 'Draft wording only. Never claim a message was published. Abstain when no authorised publication feed exists for the corridor.',
  },
};

/* Tool catalogs, locked action families and default policy notes, per desk.
   atlas/catalog.ts (ATLAS_TOOL_CATALOG, ATLAS_LOCKED, defaultAtlasProfile)
   aqua/catalog.ts  (AQUA_TOOL_CATALOG,  AQUA_LOCKED,  defaultAquaProfile) */
const DESK_CATALOGS = {
  atlas: {
    families: 'confirm_corridor · hold_no_change · note_events_only',
    tools: [
      { name: 'list_atlas_evidence', req: 'REQ', desc: 'See every observation ATLAS is allowed to read in this snapshot.' },
      { name: 'get_evidence', req: '', desc: 'Open a single permitted evidence record by id.' },
      { name: 'compare_to_freeflow', req: '', desc: 'Current probe speed divided by free-flow speed.' },
      { name: 'search_policies', req: '', desc: 'Search department, city, county, and state policy notes the operator loaded.' },
      { name: 'get_policy', req: '', desc: 'Read a full policy note by id. Policy is reference, never evidence.' },
      { name: 'propose_action', req: 'REQ', desc: 'Pick a playbook-safe family. ATLAS cannot invent a field action.' },
      { name: 'draft_finding', req: 'REQ', desc: 'Write the cited finding the operator reviews.' },
    ],
    policies: [
      { id: 'policy:dept-signals', jurisdiction: 'department', title: 'Signal timing stays with Traffic Engineering' },
      { id: 'policy:state-algo', jurisdiction: 'state', title: 'ALDOT traveler messages are state-operated' },
      { id: 'policy:county-closures', jurisdiction: 'county', title: 'Published closures are the restriction record' },
    ],
  },
  aqua: {
    families: 'confirm_lot_shuttle · hold_for_occupancy · note_shuttles_only',
    tools: [
      { name: 'list_aqua_evidence', req: 'REQ', desc: 'See shuttle and lot observations AQUA is allowed to read.' },
      { name: 'get_evidence', req: '', desc: 'Open a single permitted parking or transit record.' },
      { name: 'search_policies', req: '', desc: 'Search department, city, county, and state parking or transit notes.' },
      { name: 'get_policy', req: '', desc: 'Read a full policy note. Policy is reference, never evidence.' },
      { name: 'propose_action', req: 'REQ', desc: 'Pick a playbook-safe family. AQUA cannot change a schedule or lot policy.' },
      { name: 'draft_finding', req: 'REQ', desc: 'Write the cited finding the operator reviews.' },
    ],
    policies: [
      { id: 'policy:dept-parking', jurisdiction: 'department', title: 'Lot policy stays with Parking & Transit' },
      { id: 'policy:city-ada', jurisdiction: 'city', title: 'ADA loading is preserved' },
      { id: 'policy:occupancy-gap', jurisdiction: 'department', title: 'Occupancy is partner-gated' },
    ],
  },
};

let LIN_SEL = null;
let LIN_EDGES = [];
let LIN_NODES = {};
let LIN_W = {};
const linW = (a, b) => LIN_W[a + '>' + b] || 1;

/* Every mounted instance shares one heartbeat: whichever tree is on screen must be
   the tree that re-measures, so the tick fans out to all of them. */
const NX_LIVE = new Set();
let NX_TICK = null;

/* Node geometry is deterministic for this layout, so the last good measurement is
   reused on re-entry: connectors paint on the first frame instead of after a measure pass. */
let LIN_POS = null;
let LIN_COLW = 0;

const OVERPASS_MIRRORS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
  'https://overpass.openstreetmap.ru/api/interpreter',
];

function osmQuery(lat, lon) {
  const d = 0.025;
  const bbox = `${(lat - d).toFixed(4)},${(lon - d).toFixed(4)},${(lat + d).toFixed(4)},${(lon + d).toFixed(4)}`;
  return `[out:json][timeout:25];(
    way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]["name"](${bbox});
    relation["route"="bus"](${bbox});
  );out geom;`;
}

const metres = (a, b) => {
  const mid = ((a[1] || 32.6) + (b[1] || 32.6)) / 2;
  const dx = (b[0] - a[0]) * 111320 * Math.cos(mid * Math.PI / 180);
  const dy = (b[1] - a[1]) * 110570;
  return Math.hypot(dx, dy);
};
const bearing = (a, b) => {
  const mid = ((a[1] || 32.6) + (b[1] || 32.6)) / 2;
  const dx = (b[0] - a[0]) * Math.cos(mid * Math.PI / 180);
  const dy = b[1] - a[1];
  return (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
};

const MAP_TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_INCIDENT = [-85.495, 32.603];
const DESK_AVATARS = {
  atlas: 'madeleine-pitts', aqua: 'maxwell-tan', sentinel: 'marco-gross',
  phoenix: 'fergus-gray', forge: 'caitlyn-king', echo: 'courtney-turner',
};
const AGENT_LOCATIONS = [
  { code: 'atlas', facility: 'City of Opelika Engineering Department', address: '710 Fox Trail, Opelika, AL 36803', lat: 32.6544675, lon: -85.3611555 },
  { code: 'aqua', facility: 'Auburn University Parking Services', address: '330 Lem Morrison Drive, Auburn, AL 36849', lat: 32.5936154, lon: -85.486734 },
  { code: 'sentinel', facility: 'Auburn University Campus Safety and Security', address: '543 W Magnolia Ave, Auburn, AL 36849', lat: 32.6059515, lon: -85.4923746 },
  { code: 'phoenix', facility: 'Auburn Fire Department Headquarters', address: '359 E Magnolia Ave, Auburn, AL 36830', lat: 32.6064596, lon: -85.4754828 },
  { code: 'forge', facility: 'City of Auburn Public Works Building', address: '4277 Wire Rd, Suite 300, Auburn, AL 36832', lat: 32.5584716, lon: -85.5653283 },
  { code: 'echo', facility: 'Lee County Emergency Management Agency', address: '908 Avenue B, Opelika, AL 36801', lat: 32.6455237, lon: -85.3791158 },
];

const escapeHtml = value => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

const MODEL_CHOICES = ['openai/gpt-oss-20b', 'openai/gpt-oss-120b', 'llama3.2', 'custom'];

class NexusWallLogic extends React.Component {
  state = { screen: this.props.screen || 'operations', now: new Date(), desk: null, deskTab: 'identity', deskRailCollapsed: false, live: getLive(), profiles: {} };
  linRef = React.createRef();
  mapRef = React.createRef();

  initMap() {
    const host = this.mapRef.current;
    if (!host || !window.L || !host.clientWidth) return;
    if (host.__nxMap) {
      this.map = host.__nxMap;
      this.syncAgentMarkers();
      return;
    }
    if (host.childElementCount) host.innerHTML = '';
    const live = buildLiveView(this.state.live || getLive());
    if (live.incidentPoint) {
      MAP_INCIDENT[0] = live.incidentPoint[0];
      MAP_INCIDENT[1] = live.incidentPoint[1];
    }
    const lat = MAP_INCIDENT[1];
    const lon = MAP_INCIDENT[0];
    const coord = `${lat.toFixed(4)} N · ${Math.abs(lon).toFixed(4)} W`;
    const m = window.L.map(host, {
      center: [32.611, -85.463],
      zoom: 12,
      zoomControl: false, attributionControl: true,
      dragging: true, scrollWheelZoom: true, doubleClickZoom: true,
      touchZoom: true, boxZoom: true, keyboard: true, tap: true,
      zoomAnimation: true, fadeAnimation: true, zoomSnap: 0.25, zoomDelta: 0.5,
    });
    window.L.tileLayer(MAP_TILES, {
      maxZoom: 19,
      detectRetina: false,
      updateWhenIdle: false,
      keepBuffer: 6,
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(m);
    this.map = m;
    host.__nxMap = m;
    this.syncMapMode();
    this.coordMarker = this.mark(m, `<div style="display: flex; align-items: center; gap: 1rem; white-space: nowrap;">
        <span style="width: 3.5rem; height: 3.5rem; border-radius: 50%; border: 0.5rem solid #FF4D4F; box-shadow: 0 0 4rem rgba(255,77,79,0.65); flex: none;"></span>
        <span style="font-family: 'JetBrains Mono', monospace; font-size: 2rem; color: #F4F2ED; text-shadow: 0 0 1rem #06070A, 0 0 0.5rem #06070A;">${coord}</span>
      </div>`, [lat, lon], [0, 0]);
    this.addProbes(m);
    this.syncAgentMarkers(m);
    this.updateScale();
    void this.loadGeo(m, host);
    this.mapObserver = new ResizeObserver(() => { if (this.map) { this.map.invalidateSize(); this.updateScale(); } });
    this.mapObserver.observe(host);
  }

  /* The wall stays locked — a stray drag on a 3.8 m panel strands the room.
     Walk-up mode hands pan and zoom to whoever is at the glass. */
  syncMapMode() {
    const m = this.map;
    if (!m || !m.dragging) return;
    const live = true;
    if (this.mapLive === live && this.zoomCtl) return;
    this.mapLive = live;
    for (const h of ['dragging', 'scrollWheelZoom', 'doubleClickZoom', 'touchZoom', 'boxZoom', 'keyboard']) {
      if (m[h]) live ? m[h].enable() : m[h].disable();
    }
    if (live && !this.zoomCtl) {
      this.zoomCtl = window.L.control.zoom({ position: 'bottomright' });
      this.zoomCtl.addTo(m);
    } else if (!live && this.zoomCtl) {
      this.zoomCtl.remove();
      this.zoomCtl = null;
    }
    m.getContainer().style.cursor = 'grab';
  }

  /* Arrows and labels are DOM, so collisions are settled from real rects, not predicted boxes.
     Text wins; arrows are dropped only while more than two survive. */
  dropCollidingArrows(chevMarkers) {
    requestAnimationFrame(() => {
      // overlay panels are DOM too: the coordinate label flips to the other side rather than print under them
      const panel = document.querySelector('[data-nx-overlays]');
      const coordEl = this.coordMarker && this.coordMarker.getElement();
      if (panel && coordEl) {
        const inner = coordEl.firstElementChild;
        if (inner) inner.style.transform = 'translateY(-50%)';
        const pr = panel.getBoundingClientRect();
        const cr = coordEl.getBoundingClientRect();
        if (cr.right > pr.left - 8 && cr.left < pr.right + 8 && cr.bottom > pr.top - 8 && cr.top < pr.bottom + 8 && inner) {
          inner.style.transform = 'translate(calc(-100% - 4rem), -50%)';
        }
      }
      const arrows = chevMarkers.map(mk => ({ mk, el: mk.getElement() })).filter(a => a.el);
      const labels = [...document.querySelectorAll('.leaflet-marker-icon')]
        .filter(el => el.textContent.trim() && !el.textContent.includes('\u25B2'))
        .map(el => el.getBoundingClientRect())
        .concat(panel ? [panel.getBoundingClientRect()] : []);
      let alive = arrows.length;
      for (const a of arrows) {
        if (alive <= 2) break;
        const r = a.el.getBoundingClientRect();
        const hit = labels.some(L => r.right > L.left - 4 && r.left < L.right + 4 && r.bottom > L.top - 4 && r.top < L.bottom + 4);
        if (hit) { a.mk.remove(); alive -= 1; }
      }
    });
  }

  liveProbes() {
    return buildLiveView(this.state.live || getLive()).probes;
  }

  frameArea(m, host) {
    const pts = [[MAP_INCIDENT[1], MAP_INCIDENT[0]]]
      .concat(AGENT_LOCATIONS.map(agent => [agent.lat, agent.lon]));
    const w = host.clientWidth, hh = host.clientHeight;
    this.mapFit = () => {
      m.fitBounds(window.L.latLngBounds(pts), {
        paddingTopLeft: [Math.round(w * 0.19), Math.round(hh * 0.12)],
        paddingBottomRight: [Math.round(w * 0.15), Math.round(hh * 0.17)],
        animate: false,
      });
      this.updateScale();
    };
    this.mapFit();
  }

  readProbeFrame(m, host) {
    const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    let inFrame = 0;
    const list = this.liveProbes().map(pr => {
      const q = m.latLngToContainerPoint([pr.lat, pr.lon]);
      const here = q.x > 0 && q.y > 0 && q.x < host.clientWidth && q.y < host.clientHeight;
      if (here) inFrame += 1;
      const d = metres([MAP_INCIDENT[0], MAP_INCIDENT[1]], [pr.lon, pr.lat]);
      const br = bearing([MAP_INCIDENT[0], MAP_INCIDENT[1]], [pr.lon, pr.lat]);
      return {
        key: pr.name,
        name: pr.name,
        read: pr.read,
        away: `${(d / 1000).toFixed(1)} km ${COMPASS[Math.round(br / 45) % 8]}`,
        mark: here ? 'in frame' : 'off frame',
        markTone: here ? '#C9CDD4' : '#6F7783',
        tone: pr.sev > 0.6 ? '#FF4D4F' : pr.sev > 0.3 ? '#F0B429' : '#2FD98A',
      };
    });
    this.setState({ layFlow: inFrame > 0, probeList: list });
  }

  resetMapView() {
    if (this.mapFit) this.mapFit();
  }

  async loadGeo(m, host) {
    this.setState({ geoPhase: 'fetching' });
    let json = null;
    for (const base of OVERPASS_MIRRORS) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 8000);
      try {
        const res = await fetch(`${base}?data=${encodeURIComponent(osmQuery(MAP_INCIDENT[1], MAP_INCIDENT[0]))}`, { signal: ctl.signal });
        if (res.ok) json = await res.json();
      } catch (err) { /* next mirror */ }
      clearTimeout(timer);
      if (json && json.elements) break;
    }
    if (!json || !json.elements || this.map !== m) {
      this.geoTries = (this.geoTries || 0) + 1;
      this.frameArea(m, host);
      this.setState({
        geoPhase: 'blocked',
        geoDetail: `retry ${this.geoTries} \u00b7 flow probes and basemap only`,
        layClosed: false, layCross: false, layDetour: false, layState: false, layCity: false, layTransit: false,
      });
      this.readProbeFrame(m, host);
      if (this.geoTries < 12) setTimeout(() => { if (this.map === m) void this.loadGeo(m, host); }, 20000);
      return;
    }
    const L = window.L;
    const ways = json.elements.filter(el => el.type === 'way' && el.geometry && el.geometry.length > 1);
    const rels = json.elements.filter(el => el.type === 'relation' && el.members);
    const lonlat = w => w.geometry.map(g => [g.lon, g.lat]);
    const latlng = cs => cs.map(c => [c[1], c[0]]);
    const drawn = { closed: [], detour: [], state: [], city: [], transit: [] };
    for (const w of ways) {
      const state = /^(I|US|AL)[\s-]/.test(w.tags.ref || '');
      const line = L.polyline(latlng(lonlat(w)), {
        color: state ? '#7C6BF0' : '#2FD98A', weight: 3, opacity: 0.45, interactive: false,
      }).addTo(m);
      drawn[state ? 'state' : 'city'].push(line);
    }
    for (const r of rels) {
      for (const mem of (r.members || [])) {
        if (!mem.geometry || mem.geometry.length < 2) continue;
        drawn.transit.push(L.polyline(mem.geometry.map(g => [g.lat, g.lon]), { color: '#4CC9F0', weight: 3, opacity: 0.5, dashArray: '4 8', interactive: false }).addTo(m));
      }
    }
    this.frameArea(m, host);

    const view = m.getBounds();
    const shows = arr => arr.some(l => { try { return view.intersects(l.getBounds()); } catch (e) { return false; } });
    this.updateScale();
    this.readProbeFrame(m, host);
    const t = new Date();
    const pad = n => String(n).padStart(2, '0');
    this.setState({
      geoPhase: 'live',
      layClosed: false,
      layCross: false,
      layDetour: false,
      layState: shows(drawn.state),
      layCity: shows(drawn.city),
      layTransit: shows(drawn.transit),
      geoDetail: `fetched ${pad(t.getHours())}:${pad(t.getMinutes())} · roads around the incident point`,
    });
  }

  addProbes(m) {
    const live = buildLiveView(this.state.live || getLive());
    if (live.incidentPoint) {
      MAP_INCIDENT[0] = live.incidentPoint[0];
      MAP_INCIDENT[1] = live.incidentPoint[1];
    }
    for (const pr of live.probes) {
      const tone = pr.sev > 0.6 ? '#FF4D4F' : pr.sev > 0.3 ? '#F0B429' : '#2FD98A';
      window.L.circleMarker([pr.lat, pr.lon], {
        radius: 10 + pr.sev * 26, color: tone, weight: 3, opacity: 0.9, fillColor: tone, fillOpacity: 0.22, interactive: false,
      }).addTo(m);
    }
  }

  agentIcon(location, tile) {
    const code = location.code;
    const avatar = `/avatars/${DESK_AVATARS[code]}.jpg`;
    const tone = tile?.statusColor || '#9BA8B4';
    const status = tile?.status || '—';
    return window.L.divIcon({
      className: 'nx-agent-marker-shell',
      iconSize: [72, 88],
      iconAnchor: [36, 78],
      popupAnchor: [0, -72],
      html: `<div class="nx-agent-marker" style="--agent-tone:${escapeHtml(tone)}">
        <span class="nx-agent-marker__halo"></span>
        <img class="nx-agent-marker__avatar" src="${avatar}" alt="" />
        <span class="nx-agent-marker__code">${escapeHtml(code.toUpperCase())}</span>
        <span class="nx-agent-marker__status" aria-hidden="true">${escapeHtml(status)}</span>
      </div>`,
    });
  }

  agentPopup(location, tile) {
    const status = tile?.status && tile.status !== '—' ? tile.status : 'Monitoring';
    const evidence = tile?.meta || 'No evidence in this snapshot';
    const role = tile?.role || DESK_PROFILES[location.code]?.role || location.code;
    return `<article class="nx-agent-popup-card">
      <header class="nx-agent-popup-card__head">
        <img src="/avatars/${DESK_AVATARS[location.code]}.jpg" alt="" />
        <span><strong>${escapeHtml(location.code.toUpperCase())}</strong><small>${escapeHtml(role)}</small></span>
      </header>
      <div class="nx-agent-popup-card__facility">${escapeHtml(location.facility)}</div>
      <address>${escapeHtml(location.address)}</address>
      <footer><span>${escapeHtml(status)}</span><span>${escapeHtml(evidence)}</span></footer>
    </article>`;
  }

  syncAgentMarkers(map = this.map) {
    if (!map || !window.L) return;
    const live = buildLiveView(this.state.live || getLive());
    const byCode = new Map((live.wallDesks || []).map(tile => [tile.code, tile]));
    if (!this.agentMarkers) this.agentMarkers = new Map();
    for (const location of AGENT_LOCATIONS) {
      const tile = byCode.get(location.code);
      const signature = [tile?.status, tile?.statusColor, tile?.meta].join('|');
      let record = this.agentMarkers.get(location.code);
      if (!record) {
        const marker = window.L.marker([location.lat, location.lon], {
          icon: this.agentIcon(location, tile),
          interactive: true,
          keyboard: true,
          riseOnHover: true,
          title: `${location.code.toUpperCase()} — ${location.facility}`,
          alt: `${location.code.toUpperCase()} at ${location.address}`,
        }).addTo(map);
        marker.bindPopup(this.agentPopup(location, tile), {
          className: 'nx-agent-popup',
          minWidth: 280,
          maxWidth: 340,
          closeButton: true,
          autoPan: true,
          autoPanPadding: [48, 48],
        });
        record = { marker, signature };
        this.agentMarkers.set(location.code, record);
      } else if (record.signature !== signature) {
        record.marker.setIcon(this.agentIcon(location, tile));
        record.marker.setPopupContent(this.agentPopup(location, tile));
        record.signature = signature;
      }
    }
  }

  mark(m, html, latlng, offset, spin) {
    const icon = window.L.divIcon({
      html: spin ? html : `<div style="transform: translateY(-50%);">${html}</div>`,
      className: '', iconSize: null, iconAnchor: offset || [0, 0],
    });
    return window.L.marker(latlng, { icon, interactive: false, keyboard: false }).addTo(m);
  }

  updateScale() {
    if (!this.map || !this.map.getCenter) return;
    const h = this.map.getSize().y;
    const a = this.map.containerPointToLatLng([0, h / 2]);
    const b = this.map.containerPointToLatLng([100, h / 2]);
    const perPx = a.distanceTo(b) / 100;
    const bar = 12 * 16 * (window.innerWidth / 3840);
    const target = perPx * bar;
    const nice = [50, 100, 200, 250, 500, 1000, 2000].reduce((x, y) => Math.abs(y - target) < Math.abs(x - target) ? y : x);
    const text = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;
    if (text !== this.state.mapScale) this.setState({ mapScale: text });
  }

  markerEl(html) {
    const el = document.createElement('div');
    el.innerHTML = html;
    return el;
  }

  componentDidMount() {
    NX_LIVE.add(this);
    this.unsubLive = subscribeLive(() => this.setState({ live: getLive() }));
    void this.hydrateProfiles();
    if (!NX_TICK) {
      NX_TICK = setInterval(() => {
        NX_LIVE.forEach(c => { c.setState({ now: new Date() }); c.initMap(); c.syncMapMode(); });
      }, 1000);
    }
    requestAnimationFrame(() => this.initMap());
    this.chaseFlow();
    this.onResize = () => this.forceUpdate();
    window.addEventListener('resize', this.onResize);
    requestAnimationFrame(() => this.forceUpdate());
  }
  componentWillUnmount() {
    NX_LIVE.delete(this);
    if (this.unsubLive) this.unsubLive();
    window.removeEventListener('resize', this.onResize);
  }

  async hydrateProfiles() {
    try {
      const [atlas, aqua] = await Promise.all([
        operationalApi.deskProfile('atlas'),
        operationalApi.deskProfile('aqua'),
      ]);
      this.setState({ profiles: { atlas, aqua } });
    } catch {
      /* keep design defaults when the profile API is quiet */
    }
  }

  linClosure(sel) {
    const on = new Set();
    if (!sel) return on;
    on.add(sel);
    const walk = (id, dir) => {
      for (const [a, b] of LIN_EDGES) {
        const from = dir > 0 ? a : b;
        const to = dir > 0 ? b : a;
        if (from === id && !on.has(to)) { on.add(to); walk(to, dir); }
      }
    };
    walk(sel, 1);
    walk(sel, -1);
    return on;
  }

  /* Keep re-measuring until the flow grid has laid out: a single post-mount frame is
     not enough, and failure must retry rather than terminate. */
  chaseFlow(frames) {
    let left = typeof frames === 'number' ? frames : 30;
    const step = () => {
      this.forceUpdate();
      left -= 1;
      if (!this.lastDim && left > 0) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  linPaths() {
    const anchor = document.querySelector('[data-lin]');
    const host = this.linRef.current || (anchor && anchor.parentElement && anchor.parentElement.parentElement);
    const blank = { dim: '', hot: '', dimA: '', hotA: '' };
    if (host && !host.__nxObserved && typeof ResizeObserver !== 'undefined') {
      host.__nxObserved = true;
      new ResizeObserver(() => window.dispatchEvent(new Event('resize'))).observe(host);
    }
    let pos = null;
    let colW = LIN_COLW;
    if (host) {
      const hb = host.getBoundingClientRect();
      if (hb.width) {
        const fresh = {};
        host.querySelectorAll('[data-lin]').forEach(el => {
          const r = el.getBoundingClientRect();
          if (!r.width) return;
          fresh[el.getAttribute('data-lin')] = {
            l: r.left - hb.left,
            r: r.right - hb.left,
            t: r.top - hb.top,
            h: r.height,
            y: r.top - hb.top + r.height / 2,
          };
        });
        if (Object.keys(fresh).length > 0) {
          pos = fresh;
          colW = host.clientWidth / 7;
          LIN_POS = fresh;
          LIN_COLW = colW;
        }
      }
    }
    if (!pos) pos = LIN_POS;
    if (!pos || !colW) {
      this.lastDim = '';
      // on the render that mounts the flow, the nodes do not exist yet — take the next frame
      if (this.state.screen === 'evidence' && !this.measurePending) {
        this.measurePending = true;
        requestAnimationFrame(() => { this.measurePending = false; this.forceUpdate(); });
      }
      return blank;
    }
    const hotSet = this.linClosure(LIN_SEL);

    /* Sankey ribbons: thickness is the number of evidence rows a citation carries, so the
       chain's weight is visible, not just its topology. Ports stack on each node's edge. */
    const edges = LIN_EDGES.filter(([a, b]) => pos[a] && pos[b]);
    const outW = {}, inW = {};
    for (const [a, b] of edges) {
      const w = linW(a, b);
      outW[a] = (outW[a] || 0) + w;
      inW[b] = (inW[b] || 0) + w;
    }
    let scale = Infinity;
    for (const id of new Set([...Object.keys(outW), ...Object.keys(inW)])) {
      const load = Math.max(outW[id] || 0, inW[id] || 0);
      if (load) scale = Math.min(scale, (pos[id].h * 0.78) / load);
    }
    if (!isFinite(scale)) scale = 3;

    const cursorOut = {}, cursorIn = {};
    const sorted = edges.slice().sort((x, y) => pos[x[1]].y - pos[y[1]].y || pos[x[0]].y - pos[y[0]].y);
    const out = { dim: '', hot: '', dimA: '', hotA: '' };
    for (const [a, b] of sorted) {
      const A = pos[a], B = pos[b];
      const w = linW(a, b) * scale;
      if (cursorOut[a] === undefined) cursorOut[a] = A.y - (outW[a] * scale) / 2;
      if (cursorIn[b] === undefined) cursorIn[b] = B.y - (inW[b] * scale) / 2;
      const a0 = cursorOut[a], a1 = a0 + w;
      const b0 = cursorIn[b], b1 = b0 + w;
      cursorOut[a] = a1;
      cursorIn[b] = b1;
      const x0 = A.r, x1 = B.l;
      const c = (x1 - x0) * 0.5;
      const ribbon = `M ${x0} ${a0} C ${x0 + c} ${a0} ${x1 - c} ${b0} ${x1} ${b0} `
        + `L ${x1} ${b1} C ${x1 - c} ${b1} ${x0 + c} ${a1} ${x0} ${a1} Z `;
      if (hotSet.has(a) && hotSet.has(b)) out.hot += ribbon; else out.dim += ribbon;
    }
    this.lastDim = out.dim;
    return out;
  }

  /* The wall stays locked — a stray drag on a 3.8 m panel strands the room.
     Walk-up mode hands pan and zoom to whoever is at the glass. */
  go(screen) {
    return () => {
      this.lastDim = '';
      this.setState({ screen });
      this.chaseFlow(30);
    };
  }

  async commitDesk() {
    const code = this.state.desk;
    if (!code) return;
    const draft = (this.state.drafts || {})[code] || {};
    if (code === 'atlas' || code === 'aqua') {
      try {
        const current = this.state.profiles?.[code] || await operationalApi.deskProfile(code);
        const enabled = current.tools.filter((tool, i) => draft[`tool:${i}`] !== false).map(tool => tool.name);
        await operationalApi.saveDeskProfile(code, {
          role: draft.role ?? current.role,
          backstory: draft.backstory ?? current.backstory,
          instructions: draft.prompt ?? current.instructions,
          llm: {
            model: draft.model ?? current.llm.model,
            temperature: Number(draft.temp ?? current.llm.temperature),
            maxTurns: Number(draft.turns ?? current.llm.maxTurns),
            timeoutMs: Number(draft.timeout ?? current.llm.timeoutMs),
          },
          enabledTools: enabled.length ? enabled : current.tools.filter(tool => tool.enabled).map(tool => tool.name),
          policies: current.policies,
        });
        await this.hydrateProfiles();
      } catch {
        /* close the overlay; the next poll shows the last saved profile */
      }
    }
    const drafts = { ...(this.state.drafts || {}) };
    delete drafts[code];
    this.setState({ desk: null, deskRailCollapsed: false, drafts });
  }

  /* Draft edits live per desk until SAVE; RESTORE drops the draft back to source values. */
  edit(field, value) {
    const code = this.state.desk;
    if (!code) return;
    const drafts = { ...(this.state.drafts || {}) };
    drafts[code] = { ...(drafts[code] || {}), [field]: value };
    this.setState({ drafts });
  }

  pick(id) {
    return () => {
      LIN_SEL = LIN_SEL === id ? null : id;
      this.forceUpdate();
    };
  }

  renderVals() {
    const pad = n => String(n).padStart(2, '0');
    const d = this.state.now;
    const s = this.state.screen;
    const vals = {
      dateLine: `CENTRAL · ${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()}`,
      clock: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
      goOps: this.go('operations'),
      goDelib: this.go('deliberation'),
      goEvidence: this.go('evidence'),
      goDecision: this.go('decision'),
      goCommit: this.go('commitments'),
      goWorkflow: this.go('workflow'),
      isOps: s === 'operations',
      isDelib: s === 'deliberation',
      isEvidence: s === 'evidence',
      showDesks: s !== 'evidence' && s !== 'deliberation' && s !== 'workflow',
      isDecision: s === 'decision',
      isCommit: s === 'commitments',
      isWorkflow: s === 'workflow',
      awaiting: (this.props.witnessState ?? 'awaiting signature') === 'awaiting signature',
      signed: (this.props.witnessState ?? 'awaiting signature') === 'signed',
      reachOverlay: this.props.reachOverlay === true,
      isWall: (this.props.displayMode ?? 'wall') !== 'walk-up',
      isWalkUp: (this.props.displayMode ?? 'wall') === 'walk-up',
      modeLabel: (this.props.displayMode ?? 'wall') === 'walk-up' ? 'VIEW walk-up' : 'VIEW wall',
      closeDesk: () => this.setState({ desk: null, deskRailCollapsed: false }),
      toggleDeskRail: () => this.setState({ deskRailCollapsed: !this.state.deskRailCollapsed }),
      deskRailCollapsed: this.state.deskRailCollapsed === true,
      goIdentity: () => this.setState({ deskTab: 'identity' }),
      goPrompt: () => this.setState({ deskTab: 'prompt' }),
      goModel: () => this.setState({ deskTab: 'model' }),
      goTools: () => this.setState({ deskTab: 'tools' }),
      goPolicies: () => this.setState({ deskTab: 'policies' }),
      togglePriority: () => this.setState({ priorityOpen: !this.state.priorityOpen }),
      priorityOpen: this.state.priorityOpen === true,
      priorityLabel: this.state.priorityOpen ? 'Close' : 'Basis',
    };

    for (const code of Object.keys(DESK_PROFILES)) {
      vals[`open_${code}`] = () => this.setState({ desk: code, deskTab: 'identity', deskRailCollapsed: false });
    }

    vals.linRef = this.linRef;
    vals.mapRef = this.mapRef;
    vals.mapScale = this.state.mapScale || '';
    const phase = this.state.geoPhase || 'idle';
    const detail = this.state.geoDetail || '';
    vals.geoStatus = phase === 'fetching' ? 'fetching road geometry from OpenStreetMap…'
      : phase === 'live' ? `OSM live · ${detail}`
      : phase === 'blocked' ? `road geometry unreachable · ${detail}`
      : 'basemap Esri World Imagery · roads OpenStreetMap';
    vals.geoBlocked = phase === 'blocked';
    vals.resetMap = () => this.resetMapView();
    vals.layClosed = this.state.layClosed === true;
    vals.layCross = this.state.layCross === true;
    vals.layDetour = this.state.layDetour === true;
    vals.layState = this.state.layState === true;
    vals.layCity = this.state.layCity === true;
    vals.layTransit = this.state.layTransit === true;
    vals.layFlow = this.state.layFlow === true;
    vals.probeList = this.state.probeList || [];
    vals.hasProbes = (this.state.probeList || []).length > 0;
    const liveNow = buildLiveView(this.state.live || getLive(), null, this.state.now.getTime());
    LIN_NODES = liveNow.lineage.nodes;
    LIN_EDGES = liveNow.lineage.edges;
    LIN_W = liveNow.lineage.weights;
    vals.lineageColumns = liveNow.lineage.columns;
    vals.record = liveNow.record;
    const paths = this.linPaths();
    vals.linDim = paths.dim;
    vals.linHot = paths.hot;
    vals.linDimArrow = paths.dimA;
    vals.linHotArrow = paths.hotA;
    const linHot = this.linClosure(LIN_SEL);
    for (const id of Object.keys(LIN_NODES)) {
      const card = LIN_NODES[id];
      vals[`sel_${id}`] = this.pick(id);
      vals[`bd_${id}`] = LIN_SEL === id ? '#F0B429'
        : linHot.has(id) ? 'rgba(240,180,41,0.55)'
        : card.tone === '#8A929C' ? 'rgba(255,255,255,0.14)' : card.tone;
    }
    const ln = LIN_SEL ? LIN_NODES[LIN_SEL] : null;
    vals.linPicked = ln !== null;
    vals.linEmpty = ln === null;
    vals.linHint = ln
      ? `${linHot.size} records on this path · arrow points to what cites it`
      : 'arrows read left to right · a record points to what cites it';
    vals.linStage = ln ? ln.stage : '';
    vals.linTone = ln ? ln.tone : '#8A929C';
    vals.linId = ln ? ln.id : '';
    vals.linSrc = ln ? ln.src : '';
    vals.linAt = ln ? ln.at : '';
    vals.linNote = ln ? ln.note : '';
    if (ln) {
      const up = LIN_EDGES.filter(e => e[1] === LIN_SEL).length;
      const down = LIN_EDGES.filter(e => e[0] === LIN_SEL).length;
      vals.linUp = up ? `${up} record${up === 1 ? '' : 's'}` : 'nothing upstream';
      vals.linDown = down ? `${down} record${down === 1 ? '' : 's'}` : 'nothing downstream';
    } else {
      vals.linUp = '';
      vals.linDown = '';
    }

    const HUE = {
      atlas: 'oklch(0.70 0.09 250)', aqua: 'oklch(0.70 0.09 195)', sentinel: 'oklch(0.70 0.09 300)',
      phoenix: '#F0B429', forge: 'oklch(0.70 0.09 95)', echo: 'oklch(0.70 0.09 150)',
    };
    for (const code of Object.keys(DESK_PROFILES)) {
      const on = this.state.desk === code;
      vals[`bg_${code}`] = on ? '#1B2331' : 'transparent';
      vals[`gut_${code}`] = on ? HUE[code] : 'rgba(255,255,255,0.16)';
    }

    const desk = this.state.desk ? DESK_PROFILES[this.state.desk] : null;
    vals.deskClosed = desk === null;
    vals.deskCode = this.state.desk || '';
    vals.deskMission = desk ? desk.mission : '';
    vals.deskConnectors = desk ? desk.connectors : '';
    const cat = this.state.desk ? DESK_CATALOGS[this.state.desk] : null;
    vals.deskTools = cat ? cat.tools : [];
    vals.deskPolicies = cat ? cat.policies : [];
    vals.deskFamilies = cat ? cat.families : '';
    const t = this.state.deskTab;
    vals.deskOpen = desk !== null;
    vals.deskName = desk ? desk.name : '';
    vals.deskKicker = desk ? desk.kicker : '';
    vals.deskTitle = desk ? `Configure ${desk.name}` : '';
    vals.deskSaveLabel = desk ? `Save ${desk.name}` : 'Save';
    vals.deskBoundary = desk ? desk.boundary : '';
    vals.deskRole = desk ? desk.role : '';
    vals.deskBackstory = desk ? desk.backstory : '';
    vals.deskInstructions = desk ? desk.instructions : '';
    vals.tabIdentity = t === 'identity';
    vals.tabPrompt = t === 'prompt';
    const editable = desk ? desk.configurable : false;
    vals.showModel = t === 'model' && editable;
    vals.showTools = t === 'tools' && editable;
    vals.showPolicies = t === 'policies' && editable;
    vals.showRuleNotice = !editable && (t === 'model' || t === 'tools' || t === 'policies');
    const code = this.state.desk;
    const draft = (this.state.drafts || {})[code] || {};
    const profile = code ? this.state.profiles?.[code] : null;
    const src0 = {
      role: profile?.role ?? (desk ? desk.role : ''),
      backstory: profile?.backstory ?? (desk ? desk.backstory : ''),
      connectors: desk ? desk.connectors : '',
      prompt: profile?.instructions ?? (desk ? desk.instructions : ''),
      model: profile?.llm.model ?? MODEL_CHOICES[0],
      temp: profile ? String(profile.llm.temperature.toFixed(2)) : '0.10',
      turns: profile ? String(profile.llm.maxTurns) : '8',
      timeout: profile ? String(profile.llm.timeoutMs) : '20000',
    };
    const val = f => (draft[f] !== undefined ? draft[f] : src0[f]);
    vals.edRole = val('role');
    vals.edBackstory = val('backstory');
    vals.edConnectors = val('connectors');
    vals.edPrompt = val('prompt');
    vals.edTemp = val('temp');
    vals.edTurns = val('turns');
    vals.edTimeout = val('timeout');
    vals.onRole = ev => this.edit('role', ev.target.value);
    vals.onBackstory = ev => this.edit('backstory', ev.target.value);
    vals.onConnectors = ev => this.edit('connectors', ev.target.value);
    vals.onPrompt = ev => this.edit('prompt', ev.target.value);
    vals.onTemp = ev => this.edit('temp', ev.target.value);
    vals.onTurns = ev => this.edit('turns', ev.target.value);
    vals.onTimeout = ev => this.edit('timeout', ev.target.value);
    vals.modelOptions = MODEL_CHOICES.map(name => {
      const on = val('model') === name;
      return {
        key: name, name,
        bg: on ? 'rgba(47,217,138,0.14)' : 'transparent',
        border: on ? '#2FD98A' : 'rgba(255,255,255,0.20)',
        ink: on ? '#2FD98A' : '#A3AAB4',
        pick: () => this.edit('model', name),
      };
    });
    vals.toolRows = (cat ? cat.tools : []).map((tool, i) => {
      const off = draft[`tool:${i}`] === false;
      return {
        key: tool.name, name: tool.name, req: tool.req, desc: tool.desc,
        state: off ? 'OFF' : 'ON',
        bg: off ? 'transparent' : 'rgba(47,217,138,0.14)',
        border: off ? 'rgba(255,255,255,0.22)' : '#2FD98A',
        ink: off ? '#8A929C' : '#2FD98A',
        toggle: () => this.edit(`tool:${i}`, off ? true : false),
      };
    });
    const dirty = Object.keys(draft).length;
    vals.dirtyLabel = dirty ? `${dirty} unsaved change${dirty === 1 ? '' : 's'}` : 'no changes';
    vals.dirtyTone = dirty ? '#F0B429' : '#8A929C';
    vals.restoreDesk = () => {
      const drafts = { ...(this.state.drafts || {}) };
      delete drafts[code];
      this.setState({ drafts });
    };
    vals.saveDesk = () => void this.commitDesk();
    vals.deskSteward = desk ? desk.role : '';
    vals.deskAvatar = code && DESK_AVATARS[code] ? `/avatars/${DESK_AVATARS[code]}.jpg` : '';
    vals.deskLogo = code ? `/icons/desks/${code}.svg` : '';

    const dtabs = { identity: 'Role', prompt: 'Prompt', model: 'Model', tools: 'Tools', policies: 'Policies' };
    for (const key of Object.keys(dtabs)) {
      const on = t === key;
      const suffix = key === 'identity' ? 'Identity' : dtabs[key];
      vals[`edge${suffix}`] = on ? '#E87722' : 'transparent';
      vals[`ink${suffix}`] = on ? '#F3EDE4' : '#8B93A0';
    }
    const keys = { operations: 'Ops', deliberation: 'Delib', evidence: 'Evidence', decision: 'Decision', commitments: 'Commit', workflow: 'Workflow' };
    for (const name of SCREENS) {
      const active = s === name;
      vals[`edge${keys[name]}`] = active ? '#E87722' : 'transparent';
      vals[`ink${keys[name]}`] = active ? '#F3EDE4' : '#8B93A0';
    }
    const live = buildLiveView(this.state.live || getLive(), null, this.state.now.getTime());
    Object.assign(vals, {
      feedLive: live.feedLive,
      feedTotal: live.feedTotal,
      feedBar: live.feedBar,
      feedDegraded: `${live.feedDegraded} degraded`,
      feedOwners: live.feedOwners,
      evidenceCount: live.evidenceCount,
      evidenceFrozen: live.evidenceFrozen,
      desksContributed: live.desksContributed,
      desksStaffed: `/ ${live.desksStaffed}`,
      desksBar: live.desksBar,
      dissentLine: `${live.dissentCount} dissent`,
      abstainLine: `${live.abstainedCount} abstained`,
      windowMinutes: live.windowMinutes,
      windowUnit: live.windowUnit,
      windowBar: live.windowBar,
      windowColor: live.windowColor,
      recStatusLine: live.recStatusLine,
      recExpires: live.recExpires,
      recExpiresRemaining: live.recExpiresRemaining,
      snapshotBasis: live.snapshotBasis,
      dissentNote: live.dissentNote,
      composeLine: live.composeLine,
      silenceLine: live.silenceLine,
      playbookLine: live.playbookLine,
      detectedLine: live.detectedLine,
      recAuthoredLine: live.recAuthoredLine,
      decidedAt: live.decidedAt,
      approvals: live.approvals,
      desks: live.desks,
      expectedEffect: live.expectedEffect,
      limitations: live.limitations,
      hashShort: live.hashShort,
      recVersion: live.recVersion,
      recState: live.recState,
      operatorName: live.operatorName,
      operatorRole: live.operatorRole,
      agencyName: live.agencyName,
      feeds: live.feeds,
      commitmentPreview: live.commitmentPreview,
      hasCommitments: live.commitmentPreview.length > 0,
      noCommitments: live.commitmentPreview.length === 0,
      commitmentsHeader: live.commitmentPreview.length
        ? `Commitments in flight · ${live.commitmentsFrom}`
        : 'Commitments in flight · none yet',
      commitmentsExecuting: live.commitmentsExecuting,
      commitmentsAcceptedCount: live.commitmentsAccepted,
      blockedCount: live.blockedCount,
      commitmentsAccepted: `/ ${live.commitmentsAccepted}`,
      blockedLine: `${live.blockedCount} blocked`,
      commitmentsFrom: live.commitmentsFrom,
      sevLabel: live.sevLabel,
      sevBg: live.sevBg,
      incidentIdLine: live.incidentIdLine,
      incidentTitle: live.incidentTitle,
      incidentOwner: live.incidentOwner,
      recVersionLabel: live.recVersionLabel,
      recMeta: live.recMeta,
      recAction: live.recAction,
      awaitBanner: live.awaitBanner,
      awaitClock: live.awaitClock,
      signedBanner: live.signedBanner,
      signedMeta: live.signedMeta,
      deskStrip: live.deskStrip,
      snapshotLine: live.snapshotLine,
      wallDesks: live.wallDesks,
      lineageColumns: live.lineage.columns,
      record: live.record,
      modeLive: live.modeLabel,
      modeColor: live.modeColor,
    });
    if (live.snapshot) {
      vals.awaiting = live.awaiting;
      vals.signed = live.signed;
    }
    return vals;
  }
}


const DESIGN_WIDTH = 3840;

export default class NexusWall extends NexusWallLogic {
  componentDidMount() {
    /* Keep the 4K wall at the authored 16px root while making ordinary review displays
       twenty percent more legible than a literal 3840-to-viewport shrink. */
    this.__prevRootFontSize = document.documentElement.style.fontSize;
    document.documentElement.style.fontSize = 'min(1rem, calc(100vw * 16 / 3200), calc(100vh * 16 / 1800))';
    if (super.componentDidMount) super.componentDidMount();
  }
  componentWillUnmount() {
    if (super.componentWillUnmount) super.componentWillUnmount();
    document.documentElement.style.fontSize = this.__prevRootFontSize || '';
  }
  render() {
    return <NexusWallTemplate vals={this.renderVals()} />;
  }
}
