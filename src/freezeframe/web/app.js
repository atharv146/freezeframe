// Freezeframe dashboard. No framework: one timeline drawn on canvases, an
// incident list, and an explanation panel fed by the local JSON API.

const $ = (id) => document.getElementById(id);
const GB = 1024 ** 3;
const MB = 1024 ** 2;
const INCIDENT_KINDS = new Set(['stall', 'hang', 'crash', 'panic', 'mark', 'hardware', 'shutdown']);
const SLOTS = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6'];

const state = {
  overview: null,
  start: 0,
  end: 0,
  rangeKey: null,
  data: null,
  selected: null, // { kind: 'incident', id } | { kind: 'moment', ts }
  appSlots: new Map(), // app -> slot index; colour follows the app, never its rank
  shownApps: [],
};

// ------------------------------------------------------------- formatting

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function clock(ts, seconds = false) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) });
}

function day(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

const when = (ts, seconds = false) => `${day(ts)} ${clock(ts, seconds)}`;

function duration(s) {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

function bytes(n) {
  if (n == null) return '–';
  if (Math.abs(n) >= GB) return `${(n / GB).toFixed(1)} GB`;
  if (Math.abs(n) >= MB) return `${Math.round(n / MB)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

const rate = (n) => `${bytes(n)}/s`;

/** Capitalises a phrase, leaving product names like "macOS" alone. */
const sentence = (t) => (/^mac|^i[A-Z]/.test(t) ? t : t[0].toUpperCase() + t.slice(1));

function cores(cpu) {
  const c = cpu / 100;
  return c >= 1.15 ? `${c.toFixed(1)} cores` : `${Math.round(cpu)}% of a core`;
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ------------------------------------------------------------------- API

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

// ----------------------------------------------------------- app colours

function colourFor(app) {
  const i = state.appSlots.get(app);
  return i === undefined ? css('--other') : css(SLOTS[i]);
}

function assignSlots(apps) {
  const visible = new Set(apps);
  for (const app of apps) {
    if (state.appSlots.has(app)) continue;
    const used = new Set(state.appSlots.values());
    let slot = SLOTS.findIndex((_, i) => !used.has(i));
    if (slot === -1) {
      // Reuse the slot of an app that isn't on screen, so nothing visible changes colour.
      const victim = [...state.appSlots.entries()].find(([a]) => !visible.has(a));
      if (!victim) continue;
      state.appSlots.delete(victim[0]);
      slot = victim[1];
    }
    state.appSlots.set(app, slot);
  }
}

// ---------------------------------------------------------------- loading

async function loadOverview() {
  state.overview = await api('/api/overview');
  renderStatus();
  renderIncidents();
}

async function loadTimeline() {
  if (!state.overview.first) return;
  const points = Math.max(200, Math.round(lanesWidth()));
  state.data = await api(`/api/timeline?start=${state.start}&end=${state.end}&points=${points}`);
  state.shownApps = pickApps(state.data.apps);
  assignSlots(state.shownApps);
  draw();
  renderRangeLabel();
}

/** The apps worth a colour: the biggest memory users plus the biggest CPU spikes. */
function pickApps(apps) {
  const out = [];
  const add = (a) => {
    if (a && !out.includes(a.app) && out.length < 6) out.push(a.app);
  };
  const byMem = [...apps].sort((a, b) => b.rss_max - a.rss_max);
  const byCpu = [...apps].sort((a, b) => b.cpu_max - a.cpu_max).filter((a) => a.cpu_max >= 25);
  for (let i = 0; i < 6; i++) {
    add(byMem[i]);
    add(byCpu[i]);
  }
  return out;
}

function setRange(start, end, key = null) {
  const { first, last } = state.overview;
  const span = Math.max(60, end - start);
  end = Math.min(end, last + span * 0.02);
  start = Math.max(start, first - span * 0.02);
  if (end - start < 60) end = start + 60;
  state.start = start;
  state.end = end;
  state.rangeKey = key;
  for (const b of document.querySelectorAll('#ranges button')) b.setAttribute('aria-pressed', String(b.dataset.range === key));
  return loadTimeline();
}

function setPreset(key) {
  const { first, last } = state.overview;
  if (key === 'all') return setRange(first, last, 'all');
  return setRange(last - Number(key), last, key);
}

function renderRangeLabel() {
  $('range-label').textContent = `${when(state.start)} to ${day(state.end) === day(state.start) ? clock(state.end) : when(state.end)}`;
}

// ---------------------------------------------------------------- status

function renderStatus() {
  const o = state.overview;
  const host = o.recorder?.host ? ` on ${esc(o.recorder.host)}` : '';
  let html;
  if (o.demo) html = `<span class="dot"></span>Demo recording: four made-up hours of a MacBook, to show what Freezeframe finds.`;
  else if (!o.first) html = `<span class="dot"></span>Nothing recorded yet.`;
  else if (o.live) html = `<span class="dot live"></span>Recording${host}. Last sample ${Math.max(0, Math.round(o.now - o.last))} s ago.`;
  else html = `<span class="dot"></span>Not recording right now. The recording ends ${esc(when(o.last))}.`;
  $('status').innerHTML = html;
}

function kindLabel(kind) {
  return {
    stall: 'Whole-computer freeze', hang: 'App stopped responding', crash: 'App crashed', panic: 'System crash',
    mark: 'You reported it', hardware: 'Hardware or driver error', shutdown: 'Unexpected restart',
  }[kind] || kind;
}

function renderIncidents() {
  const list = $('incident-list');
  const items = state.overview.incidents;
  if (!items.length) {
    list.innerHTML = `<li class="empty">No incidents yet. When something freezes, it shows up here, or press “Something went wrong” to look at the last few minutes.</li>`;
    return;
  }
  list.innerHTML = items.map((e) => `
    <li><button type="button" class="incident" data-id="${e.id}" aria-current="${state.selected?.kind === 'incident' && state.selected.id === e.id}">
      <span class="when">${esc(when(e.ts))}</span>
      <span class="title">${esc(e.title)}</span>
      <span class="kind"><span class="sev-icon sev-${Math.min(3, e.severity)}" aria-hidden="true"></span>${esc(kindLabel(e.kind))}${e.confidence ? `, ${esc(e.confidence)} confidence` : ''}</span>
    </button></li>`).join('');
  for (const b of list.querySelectorAll('.incident')) b.addEventListener('click', () => selectIncident(Number(b.dataset.id)));
}

// --------------------------------------------------------------- explain

async function selectIncident(id, moveView = true) {
  state.selected = { kind: 'incident', id };
  renderIncidents();
  const e = state.overview.incidents.find((x) => x.id === id);
  if (e && moveView) {
    const end = Math.max(e.end_ts || e.ts, e.ts);
    await setRange(e.ts - 12 * 60, end + 4 * 60, null);
  }
  const r = await api(`/api/diagnose?id=${id}`);
  renderExplain(r.diagnosis, r.event);
  history.replaceState(null, '', `#incident=${id}`);
}

async function selectMoment(ts) {
  state.selected = { kind: 'moment', ts };
  renderIncidents();
  draw();
  const r = await api(`/api/diagnose?at=${ts}`);
  renderExplain(r.diagnosis, null, ts);
}

function confidenceHtml(c) {
  const n = { high: 3, medium: 2, low: 1 }[c] || 1;
  return `<span class="confidence"><span class="meter" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</span>${esc(c[0].toUpperCase() + c.slice(1))} confidence</span>`;
}

function renderExplain(d, event, moment) {
  const top = d.findings[0];
  let whenLine;
  if (event) {
    const dur = (event.end_ts || event.ts) - event.ts;
    whenLine = `${when(event.ts, true)}${event.kind === 'stall' ? `, frozen for ${duration(dur)}` : ''}${event.kind === 'mark' && event.detail?.note ? `, you wrote “${esc(event.detail.note)}”` : ''}`;
  } else {
    whenLine = `What was happening around ${when(moment, true)}`;
  }
  const list = (items) => `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const others = d.findings.slice(1);
  const apps = d.culprits.filter((a) => a.rss > 0 || a.cpu_avg > 0);
  const report = event?.detail?.report;
  $('explain').innerHTML = `
    <p class="when">${whenLine}</p>
    <h2>${esc(top.headline)}</h2>
    ${top.score > 0 ? confidenceHtml(d.confidence) : ''}
    <p class="lead">${esc(top.explanation)}</p>
    ${top.evidence.length ? `<h3>What the recording shows</h3>${list(top.evidence)}` : ''}
    ${top.actions.length ? `<h3>What you can do</h3>${list(top.actions)}` : ''}
    ${others.length ? `<h3>Also noticed</h3><ul class="also">${others.map((f) => `<li>${esc(f.headline)} <span class="tag">${f.score >= 0.5 ? 'likely contributed' : 'possibly related'}</span></li>`).join('')}</ul>` : ''}
    ${apps.length ? `<h3>Busiest apps in the 2 minutes before</h3>
      <table class="apps"><thead><tr><th scope="col">App</th><th scope="col" class="num">Memory</th><th scope="col" class="num">Change</th><th scope="col" class="num">CPU</th></tr></thead>
      <tbody>${apps.map((a) => `<tr>
        <td><span class="app-name"><span class="swatch" style="background:${colourFor(a.app)}"></span>${esc(a.app)}</span>${a.what ? `<span class="what">${esc(sentence(a.what))}</span>` : ''}</td>
        <td class="num">${bytes(a.rss)}</td>
        <td class="num">${Math.abs(a.growth) >= 100 * MB ? (a.growth > 0 ? '+' : '−') + bytes(Math.abs(a.growth)) : ''}</td>
        <td class="num">${cores(a.cpu_avg)}</td></tr>`).join('')}</tbody></table>` : ''}
    ${d.related.length ? `<h3>Around the same time</h3><ul class="related">${d.related.slice(0, 8).map((r) => `<li><span class="t">${esc(clock(r.ts))}</span><span>${esc(r.title)}</span></li>`).join('')}</ul>` : ''}
    ${report ? `<h3>The system's own report</h3><p class="report-path">${esc(report)}</p>` : ''}`;
}

function renderWelcome() {
  $('explain').innerHTML = `<div class="welcome">
    <h2>Nothing recorded yet</h2>
    <p class="lead">Freezeframe needs to be running when a freeze happens. Start it in the background, and it will start again every time you log in:</p>
    <p><code>freezeframe install</code></p>
    <p>Then come back here, or run <code>freezeframe why</code> in a terminal, the next time something goes wrong. To see what it can do right now, run <code>freezeframe open --demo</code>.</p>
  </div>`;
}

// ---------------------------------------------------------------- drawing

const LANES = ['events', 'cpu', 'mem', 'swap', 'disk', 'freeze'];
const canvasOf = (lane) => document.querySelector(`.lane[data-lane="${lane}"] canvas`);

function lanesWidth() {
  return canvasOf('cpu').getBoundingClientRect().width;
}

function prep(lane) {
  const c = canvasOf(lane);
  const rect = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.round(rect.width * dpr);
  c.height = Math.round(rect.height * dpr);
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, rect.width, rect.height);
  return { g, w: rect.width, h: rect.height };
}

const xOf = (t, w) => ((t - state.start) / (state.end - state.start)) * w;

function tickStep(span) {
  const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
  return steps.find((s) => span / s <= 8) || 86400;
}

function verticalGrid(g, w, h) {
  const step = tickStep(state.end - state.start);
  const offset = new Date().getTimezoneOffset() * 60;
  g.strokeStyle = css('--grid');
  g.lineWidth = 1;
  for (let t = Math.ceil((state.start - offset) / step) * step + offset; t <= state.end; t += step) {
    const x = Math.round(xOf(t, w)) + 0.5;
    g.beginPath();
    g.moveTo(x, 0);
    g.lineTo(x, h);
    g.stroke();
  }
}

function incidentBands(g, w, h) {
  if (!state.data) return;
  for (const e of state.data.events) {
    if (e.kind === 'stall') {
      const x0 = xOf(e.ts, w);
      const x1 = Math.max(x0 + 3, xOf(e.end_ts || e.ts, w));
      g.fillStyle = css('--band');
      g.fillRect(x0, 0, x1 - x0, h);
      g.fillStyle = css('--critical');
      g.fillRect(x0, 0, Math.max(1.5, Math.min(2, x1 - x0)), h);
    } else if (e.kind === 'mark') {
      const x = Math.round(xOf(e.ts, w)) + 0.5;
      g.strokeStyle = css('--ink');
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(x, 0);
      g.lineTo(x, h);
      g.stroke();
      g.setLineDash([]);
    }
  }
  if (state.selected?.kind === 'moment') {
    const x = Math.round(xOf(state.selected.ts, w)) + 0.5;
    g.strokeStyle = css('--s1');
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(x, 0);
    g.lineTo(x, h);
    g.stroke();
    g.lineWidth = 1;
  }
}

function sleepBands(g, w, h) {
  for (const e of state.data.events) {
    if (e.kind !== 'sleep' && e.kind !== 'gap') continue;
    const x0 = xOf(e.ts, w);
    const x1 = xOf(e.end_ts || e.ts, w);
    g.save();
    g.beginPath();
    g.rect(x0, 0, x1 - x0, h);
    g.clip();
    g.strokeStyle = css('--grid');
    for (let x = x0 - h; x < x1; x += 7) {
      g.beginPath();
      g.moveTo(x, h);
      g.lineTo(x + h, 0);
      g.stroke();
    }
    g.restore();
  }
}

function axisLabel(g, text, x = 6, y = 12) {
  g.font = `12px ${css('--mono')}`;
  const w = g.measureText(text).width;
  g.fillStyle = css('--surface');
  g.globalAlpha = 0.85;
  g.fillRect(x - 3, y - 11, w + 6, 15);
  g.globalAlpha = 1;
  g.fillStyle = css('--ink-3');
  g.fillText(text, x, y);
}

/** Splits buckets wherever data is missing (sleep, recorder off) so lines break instead of bridging. */
function segments(series) {
  const out = [];
  let cur = [];
  const gap = state.data.step * 2.5;
  for (const p of series) {
    if (cur.length && p.t - cur[cur.length - 1].t > gap) {
      out.push(cur);
      cur = [];
    }
    cur.push(p);
  }
  if (cur.length) out.push(cur);
  return out;
}

function stacked(lane, totalOf, valueOf, maxValue, label) {
  const { g, w, h } = prep(lane);
  const top = 6;
  const plotH = h - top - 2;
  verticalGrid(g, w, h);
  sleepBands(g, w, h);
  const series = state.data.series;
  const per = state.data.app_series;
  const apps = state.shownApps;
  const byApp = apps.map((a) => new Map((per[a] || []).map((p) => [p.t, p])));
  const y = (v) => top + plotH - (Math.min(v, maxValue) / maxValue) * plotH;
  const surface = css('--surface');

  for (const seg of segments(series)) {
    const layers = seg.map((p) => {
      const vals = byApp.map((m) => valueOf(m.get(p.t)));
      const total = totalOf(p);
      const sum = vals.reduce((a, b) => a + b, 0);
      return { t: p.t, vals: [...vals, Math.max(0, total - sum)] };
    });
    const colours = [...apps.map(colourFor), css('--other')];
    const base = layers.map(() => 0);
    for (let i = 0; i < colours.length; i++) {
      const lower = base.slice();
      const upper = layers.map((l, k) => lower[k] + l.vals[i]);
      g.beginPath();
      layers.forEach((l, k) => (k ? g.lineTo : g.moveTo).call(g, xOf(l.t, w), y(upper[k])));
      for (let k = layers.length - 1; k >= 0; k--) g.lineTo(xOf(layers[k].t, w), y(lower[k]));
      g.closePath();
      g.fillStyle = colours[i];
      g.fill();
      // A thin surface-coloured edge separates neighbouring layers.
      g.beginPath();
      layers.forEach((l, k) => (k ? g.lineTo : g.moveTo).call(g, xOf(l.t, w), y(upper[k])));
      g.strokeStyle = surface;
      g.lineWidth = 1.5;
      g.stroke();
      for (let k = 0; k < base.length; k++) base[k] = upper[k];
    }
  }
  incidentBands(g, w, h);
  axisLabel(g, label);
  return { g, w, h };
}

function line(lane, valueOf, floor, unit = rate) {
  const { g, w, h } = prep(lane);
  verticalGrid(g, w, h);
  sleepBands(g, w, h);
  const series = state.data.series;
  // A floor on the scale keeps everyday background noise flat instead of filling the lane.
  const max = Math.max(floor, ...series.map(valueOf));
  const top = 6;
  const y = (v) => top + (h - top - 2) - (v / max) * (h - top - 2);
  g.strokeStyle = css('--ink-2');
  g.fillStyle = css('--grid');
  g.lineWidth = 2;
  g.lineJoin = 'round';
  for (const seg of segments(series)) {
    g.beginPath();
    seg.forEach((p, k) => (k ? g.lineTo : g.moveTo).call(g, xOf(p.t, w), y(valueOf(p))));
    g.stroke();
  }
  incidentBands(g, w, h);
  axisLabel(g, unit(max));
}

function drawEvents() {
  const { g, w, h } = prep('events');
  verticalGrid(g, w, h);
  sleepBands(g, w, h);
  const cy = h - 13;
  const evs = state.data.events.filter((e) => e.kind !== 'sleep' && e.kind !== 'gap');
  for (const e of [...evs].sort((a, b) => a.severity - b.severity)) {
    const x = xOf(e.ts, w);
    const sev = Math.min(3, e.severity);
    g.fillStyle = sev >= 3 ? css('--critical') : sev === 2 ? css('--serious') : css('--ink-3');
    g.strokeStyle = css('--surface');
    g.lineWidth = 2;
    g.beginPath();
    if (sev >= 3) {
      g.moveTo(x, cy - 7);
      g.lineTo(x + 7, cy + 6);
      g.lineTo(x - 7, cy + 6);
      g.closePath();
    } else if (sev === 2) {
      g.rect(x - 5, cy - 5, 10, 10);
    } else {
      g.arc(x, cy, 4, 0, Math.PI * 2);
    }
    g.stroke();
    g.fill();
  }
  // Time axis labels along the top lane.
  const step = tickStep(state.end - state.start);
  const offset = new Date().getTimezoneOffset() * 60;
  g.font = `12px ${css('--sans')}`;
  g.fillStyle = css('--ink-3');
  for (let t = Math.ceil((state.start - offset) / step) * step + offset; t <= state.end; t += step) {
    const x = xOf(t, w);
    if (x < 30 || x > w - 40) continue;
    g.fillText(step >= 86400 ? day(t) : clock(t), x + 4, 12);
  }
}

function drawFreezes() {
  const { g, w, h } = prep('freeze');
  verticalGrid(g, w, h);
  sleepBands(g, w, h);
  // Heat: status bands along the bottom.
  for (const seg of segments(state.data.series)) {
    for (let k = 0; k < seg.length; k++) {
      const p = seg[k];
      if (!p.thermal) continue;
      const x0 = xOf(p.t, w);
      const x1 = xOf((seg[k + 1] || p).t + (seg[k + 1] ? 0 : state.data.step), w);
      g.fillStyle = p.thermal >= 2 ? css('--serious') : css('--warning');
      g.fillRect(x0, h - 12, Math.max(1, x1 - x0), 10);
    }
  }
  incidentBands(g, w, h);
  g.font = `600 12px ${css('--sans')}`;
  for (const e of state.data.events) {
    if (e.kind !== 'stall') continue;
    const x0 = xOf(e.ts, w);
    const dur = (e.end_ts || e.ts) - e.ts;
    const bh = Math.min(h - 20, 8 + dur * 2.5);
    g.fillStyle = css('--critical');
    g.fillRect(x0, h - 14 - bh, Math.max(4, xOf(e.end_ts, w) - x0), bh);
    g.fillStyle = css('--ink');
    g.fillText(duration(dur), x0 + 7, h - 16 - bh + 10);
  }
}

function draw() {
  if (!state.data) return;
  const ncpu = state.overview.recorder?.cpus || navigator.hardwareConcurrency || 8;
  drawEvents();
  stacked('cpu', (p) => p.cpu, (a) => (a ? a.cpu / ncpu : 0), 100, '100%');
  const memMax = Math.max(...state.data.series.map((p) => p.mem_total || 0), GB);
  const mem = stacked('mem', (p) => p.mem_total - p.mem_available, (a) => (a ? a.rss : 0), memMax, bytes(memMax));
  // Memory pressure as a status strip across the top of the memory lane.
  for (const seg of segments(state.data.series)) {
    seg.forEach((p, k) => {
      if (!p.pressure || p.pressure < 2) return;
      const x0 = xOf(p.t, mem.w);
      const x1 = xOf((seg[k + 1] || p).t + (seg[k + 1] ? 0 : state.data.step), mem.w);
      mem.g.fillStyle = p.pressure >= 4 ? css('--critical') : css('--warning');
      mem.g.fillRect(x0, 0, Math.max(1, x1 - x0), 4);
    });
  }
  line('swap', (p) => (p.swap_in || 0) + (p.swap_out || 0), 20 * MB);
  line('disk', (p) => Math.max(p.disk_read || 0, p.disk_write || 0), 60 * MB);
  drawFreezes();
  renderLegend();
}

function renderLegend() {
  $('legend').innerHTML = [
    ...state.shownApps.map((a) => `<span class="item"><span class="swatch" style="background:${colourFor(a)}"></span>${esc(a)}</span>`),
    `<span class="item"><span class="swatch" style="background:${css('--other')}"></span>Everything else</span>`,
    `<span class="item status-key"><span class="swatch" style="background:${css('--critical')}"></span>Freeze</span>`,
    `<span class="item status-key"><span class="swatch" style="background:${css('--warning')}"></span>Memory pressure or heat, rising</span>`,
    `<span class="item status-key"><span class="swatch" style="background:${css('--serious')}"></span>Heat, severe</span>`,
  ].join('');
}

// ------------------------------------------------------------ interaction

const lanesEl = $('lanes');
const tip = $('tip');
let drag = null;

function eventTime(ev) {
  const r = canvasOf('cpu').getBoundingClientRect();
  const x = Math.max(0, Math.min(r.width, ev.clientX - r.left));
  return { t: state.start + (x / r.width) * (state.end - state.start), x, left: r.left - lanesEl.getBoundingClientRect().left };
}

function nearestBucket(t) {
  const s = state.data?.series || [];
  let best = null;
  for (const p of s) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  return best && Math.abs(best.t - t) <= state.data.step * 2 ? best : null;
}

function showTip(ev) {
  if (!state.data) return;
  const { t, x, left } = eventTime(ev);
  const ch = $('crosshair');
  ch.hidden = false;
  ch.style.left = `${left + x}px`;
  const lane = ev.target.closest('.lane')?.dataset.lane;
  let html;
  if (lane === 'events' || lane === 'freeze') {
    const w = canvasOf('cpu').getBoundingClientRect().width;
    const near = state.data.events.filter((e) => e.kind !== 'sleep' && Math.abs(xOf(e.ts, w) - x) <= 8);
    if (near.length) html = near.slice(0, 4).map((e) => `<div class="t">${esc(clock(e.ts, true))}</div><div>${esc(e.title)}</div>`).join('');
  }
  if (!html) {
    const p = nearestBucket(t);
    if (!p) {
      tip.hidden = true;
      return;
    }
    const ncpu = state.overview.recorder?.cpus || 8;
    const per = state.data.app_series;
    const appsAt = state.shownApps
      .map((a) => ({ a, v: (per[a] || []).find((q) => q.t === p.t) }))
      .filter((x) => x.v)
      .sort((m, n) => n.v.cpu - m.v.cpu)
      .slice(0, 4);
    const used = p.mem_total - p.mem_available;
    html = `<div class="t">${esc(when(p.t, true))}</div>
      <div class="row"><span>CPU</span><span>${Math.round(p.cpu)}%</span></div>
      ${appsAt.map(({ a, v }) => `<div class="row"><span><span class="sw" style="background:${colourFor(a)}"></span>${esc(a)}</span><span>${cores(v.cpu)}, ${bytes(v.rss)}</span></div>`).join('')}
      <div class="row"><span>Memory in use</span><span>${bytes(used)} of ${bytes(p.mem_total)}</span></div>
      ${p.pressure >= 2 ? `<div class="row"><span>Memory pressure</span><span>${p.pressure >= 4 ? 'critical' : 'warning'}</span></div>` : ''}
      <div class="row"><span>Swapping</span><span>${rate((p.swap_in || 0) + (p.swap_out || 0))}</span></div>
      <div class="row"><span>Disk</span><span>${rate(Math.max(p.disk_read || 0, p.disk_write || 0))}</span></div>
      ${p.thermal ? `<div class="row"><span>Heat</span><span>${p.thermal >= 2 ? 'throttling hard' : 'warm, slowing down'}</span></div>` : ''}`;
  }
  tip.innerHTML = html;
  tip.hidden = false;
  const tx = ev.clientX + 16 + tip.offsetWidth > window.innerWidth ? ev.clientX - tip.offsetWidth - 16 : ev.clientX + 16;
  tip.style.left = `${tx}px`;
  tip.style.top = `${Math.min(ev.clientY + 12, window.innerHeight - tip.offsetHeight - 8)}px`;
}

lanesEl.addEventListener('pointermove', (ev) => {
  if (drag) {
    const { x, left } = eventTime(ev);
    drag.x1 = x;
    const sel = $('drag');
    sel.hidden = Math.abs(drag.x1 - drag.x0) < 5;
    sel.style.left = `${left + Math.min(drag.x0, drag.x1)}px`;
    sel.style.width = `${Math.abs(drag.x1 - drag.x0)}px`;
  }
  showTip(ev);
});

lanesEl.addEventListener('pointerleave', () => {
  $('crosshair').hidden = true;
  tip.hidden = true;
});

lanesEl.addEventListener('pointerdown', (ev) => {
  if (!state.data || !ev.target.matches('canvas')) return;
  const { x, t } = eventTime(ev);
  drag = { x0: x, x1: x, t0: t };
  lanesEl.setPointerCapture(ev.pointerId);
});

lanesEl.addEventListener('pointerup', (ev) => {
  if (!drag) return;
  const d = drag;
  drag = null;
  $('drag').hidden = true;
  const { t } = eventTime(ev);
  if (Math.abs(d.x1 - d.x0) >= 5) {
    setRange(Math.min(d.t0, t), Math.max(d.t0, t));
    return;
  }
  // A click on an event marker opens that incident; anywhere else explains the moment.
  const w = canvasOf('cpu').getBoundingClientRect().width;
  const hit = state.data.events.find((e) => INCIDENT_KINDS.has(e.kind) && Math.abs(xOf(e.ts, w) - d.x0) <= 6);
  if (hit && state.overview.incidents.some((i) => i.id === hit.id)) selectIncident(hit.id, false);
  else selectMoment(t);
});

for (const b of document.querySelectorAll('#ranges button')) b.addEventListener('click', () => setPreset(b.dataset.range));
$('pan-back').addEventListener('click', () => {
  const span = state.end - state.start;
  setRange(state.start - span * 0.5, state.end - span * 0.5);
});
$('pan-fwd').addEventListener('click', () => {
  const span = state.end - state.start;
  setRange(state.start + span * 0.5, state.end + span * 0.5);
});

// ------------------------------------------------------------------- mark

$('mark-open').addEventListener('click', () => {
  $('mark-form').hidden = false;
  $('mark-note').focus();
});
$('mark-cancel').addEventListener('click', () => {
  $('mark-form').hidden = true;
});
$('mark-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const minutes = Number($('mark-when').value);
  const ts = (state.overview.demo ? state.overview.last : Date.now() / 1000) - minutes * 60;
  const r = await api('/api/mark', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: $('mark-note').value, ts }) });
  $('mark-form').hidden = true;
  $('mark-note').value = '';
  await loadOverview();
  await selectIncident(r.event.id);
});

// ------------------------------------------------------------------ start

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(draw, 120);
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  draw();
  renderLegend();
});

async function refreshLive() {
  if (!state.overview?.live) return;
  const followed = state.overview.last - state.end < 60;
  const span = state.end - state.start;
  await loadOverview();
  if (followed && !drag) await setRange(state.overview.last - span, state.overview.last, state.rangeKey);
}

async function main() {
  await loadOverview();
  const o = state.overview;
  if (!o.first) {
    renderWelcome();
    return;
  }
  const fromHash = Number(new URLSearchParams(location.hash.slice(1)).get('incident'));
  if (fromHash && o.incidents.some((i) => i.id === fromHash)) {
    await selectIncident(fromHash);
  } else {
    await setPreset(o.last - o.first <= 6 * 3600 ? 'all' : '3600');
    const recent = o.incidents.filter((i) => i.ts >= o.first);
    const pick = recent.slice(0, 6).sort((a, b) => b.severity - a.severity || b.ts - a.ts)[0];
    if (pick) {
      state.selected = { kind: 'incident', id: pick.id };
      renderIncidents();
      const r = await api(`/api/diagnose?id=${pick.id}`);
      renderExplain(r.diagnosis, r.event);
      draw();
    }
  }
  setInterval(refreshLive, 5000);
}

main().catch((e) => {
  $('explain').innerHTML = `<h2>Couldn't load the recording</h2><p class="lead">${esc(e.message)}</p>`;
});
