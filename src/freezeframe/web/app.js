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
  ask: null, // { start, end } of the latest "What happened at" question, shaded on the timeline
  strip: null,
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

/** CPU given as percent of one core, shown as a share of the whole computer. */
function ncores() {
  return state.data?.cpus || state.overview?.recorder?.cpus || navigator.hardwareConcurrency || 8;
}

function pct(cpuOfOneCore, n = ncores()) {
  const v = cpuOfOneCore / n;
  return v < 0.95 ? `${v.toFixed(1)}%` : `${Math.round(v)}%`;
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ------------------------------------------------------------------- API

async function api(path, options) {
  // The public website has no server; a static stand-in answers from exported data.
  if (window.freezeframeApi) return window.freezeframeApi(path, options);
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
  renderRangeSummary();
  drawStrip();
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
  $('range-label').textContent = `Showing ${when(state.start)} to ${day(state.end) === day(state.start) ? clock(state.end) : when(state.end)} (${duration(state.end - state.start)})`;
}

// ------------------------------------------------------- range summary

/** Plain numbers for the stretch on screen, the way "Right now" shows the present. */
function renderRangeSummary() {
  const d = state.data;
  const el = $('range-summary');
  if (!d || !d.series.length) {
    el.innerHTML = '<p class="section-hint">Nothing was recorded in this stretch.</p>';
    return;
  }
  const n = d.series.length;
  const kindAt = new Map((d.kind_series || []).map((k) => [k.t, k]));
  const avg = d.series.reduce((t, p) => t + p.cpu, 0) / n;
  const peak = d.series.reduce((b, p) => ((p.cpu_max ?? p.cpu) > (b.cpu_max ?? b.cpu) ? p : b), d.series[0]);
  const apps = d.series.reduce((t, p) => t + Math.min(p.cpu, kindAt.get(p.t)?.apps ?? 0), 0) / n;
  const memPeak = d.series.reduce((b, p) => (p.mem_total - p.mem_available > b.mem_total - b.mem_available ? p : b), d.series[0]);
  const problems = d.events.filter((e) => INCIDENT_KINDS.has(e.kind));
  const busiest = [...d.apps].sort((a, b) => b.cpu_avg - a.cpu_avg)[0];
  const peakWho = kindAt.get(peak.t)?.top;
  const tiles = [
    { label: 'Processor', value: `${Math.round(avg)}%`, sub: `average; peak ${Math.round(peak.cpu_max ?? peak.cpu)}% at ${clock(peak.t)}${peakWho ? `, mostly ${peakWho}` : ''}`, ts: peak.t },
    { label: 'Your apps', value: `${Math.round(apps)}%`, sub: 'of the processor, on average' },
    { label: 'Background and system', value: `${Math.round(Math.max(0, avg - apps))}%`, sub: 'of the processor, on average' },
    { label: 'Memory', value: bytes(memPeak.mem_total - memPeak.mem_available), sub: `at most, of ${bytes(memPeak.mem_total)}`, ts: memPeak.t },
    { label: 'Problems', value: String(problems.length), sub: problems.length ? 'freezes, hangs or crashes; use Next problem to step through' : 'no freezes, hangs or crashes' },
  ];
  if (busiest && busiest.cpu_avg >= 1) tiles.splice(3, 0, { label: 'Busiest app', value: pct(busiest.cpu_avg), sub: busiest.app, small: true });
  el.innerHTML = `<div class="readouts compact">${tiles.map((t) => `<div class="readout${t.ts ? ' jump' : ''}"${t.ts ? ` data-ts="${t.ts}" role="button" tabindex="0" title="Show this moment"` : ''}>
    <div class="label">${esc(t.label)}</div><div class="value">${esc(t.value)}</div><div class="sub">${esc(t.sub)}</div></div>`).join('')}</div>`;
  for (const t of el.querySelectorAll('[data-ts]')) {
    const go = () => selectMoment(Number(t.dataset.ts));
    t.addEventListener('click', go);
    t.addEventListener('keydown', (ev) => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), go()));
  }
}

// --------------------------------------------------------------- strip

async function loadStrip() {
  if (!state.overview?.first) return;
  if (state.strip && state.strip.loaded > Date.now() - 60000) return drawStrip();
  const w = $('strip').getBoundingClientRect().width || 800;
  state.strip = { ...(await api(`/api/strip?points=${Math.round(Math.min(1000, w))}`)), loaded: Date.now() };
  drawStrip();
}

/** The whole recording in one thin strip, with the part on screen highlighted. */
function drawStrip() {
  const st = state.strip;
  const c = $('strip').querySelector('canvas');
  if (!st || !st.first || currentView !== 'timeline') return;
  const rect = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.round(rect.width * dpr);
  c.height = Math.round(rect.height * dpr);
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = rect.width;
  const h = rect.height;
  const lo = st.first;
  const hi = Math.max(st.last, st.first + 60);
  const x = (t) => ((t - lo) / (hi - lo)) * w;
  g.clearRect(0, 0, w, h);
  g.fillStyle = css('--other');
  for (const p of st.series) {
    const bh = Math.max(1, (p.cpu / 100) * (h - 14));
    g.fillRect(x(p.t), h - bh, Math.max(1, (st.step / (hi - lo)) * w), bh);
    if (p.pressure >= 2) {
      g.fillStyle = p.pressure >= 4 ? css('--critical') : css('--warning');
      g.fillRect(x(p.t), 0, Math.max(1, (st.step / (hi - lo)) * w), 3);
      g.fillStyle = css('--other');
    }
  }
  for (const e of state.overview.incidents) {
    if (e.ts < lo || e.ts > hi) continue;
    g.fillStyle = e.severity >= 3 ? css('--critical') : css('--serious');
    g.fillRect(Math.round(x(e.ts)) - 1, 4, 2, h - 4);
  }
  g.font = `11px ${css('--sans')}`;
  g.fillStyle = css('--ink-3');
  g.fillText(when(lo), 4, 12);
  const end = state.overview.live ? 'now' : when(hi);
  g.fillText(end, w - g.measureText(end).width - 4, 12);
  const win = $('strip-window');
  const x0 = Math.max(0, x(state.start));
  const x1 = Math.min(w, x(state.end));
  win.style.left = `${x0}px`;
  win.style.width = `${Math.max(6, x1 - x0)}px`;
}

{
  const strip = $('strip');
  let dragStrip = null;
  const timeAt = (ev) => {
    const r = strip.getBoundingClientRect();
    const st = state.strip;
    return st.first + ((ev.clientX - r.left) / r.width) * (Math.max(st.last, st.first + 60) - st.first);
  };
  strip.addEventListener('pointerdown', (ev) => {
    if (!state.strip?.first) return;
    const t = timeAt(ev);
    const span = state.end - state.start;
    const inside = t >= state.start && t <= state.end;
    dragStrip = { offset: inside ? t - state.start : span / 2, span };
    if (!inside) setRange(t - span / 2, t + span / 2, null);
    try {
      strip.setPointerCapture(ev.pointerId);
    } catch {
      // Dragging still works without capture.
    }
  });
  strip.addEventListener('pointermove', (ev) => {
    if (!dragStrip) return;
    const start = timeAt(ev) - dragStrip.offset;
    state.start = start;
    state.end = start + dragStrip.span;
    drawStrip(); // move the highlight instantly; the charts follow when the drag ends
  });
  strip.addEventListener('pointerup', () => {
    if (!dragStrip) return;
    dragStrip = null;
    setRange(state.start, state.end, null);
  });
}

// ---------------------------------------------------------------- status

function renderStatus() {
  const o = state.overview;
  const host = o.recorder?.host ? ` on ${esc(o.recorder.host)}` : '';
  let html;
  if (o.site) html = `<span class="dot"></span>Live demo with four made-up hours of a MacBook. <a href="https://github.com/atharv146/freezeframe#install">Install Freezeframe</a> to record your own computer.`;
  else if (o.demo) html = `<span class="dot"></span>Demo recording: four made-up hours of a MacBook, to show what Freezeframe finds.`;
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

// ------------------------------------------------------------- right now

let currentView = 'now';

function setTabs(v) {
  currentView = v;
  $('tip').hidden = true;
  for (const name of ['now', 'timeline', 'computer']) {
    $(`tab-${name}`).setAttribute('aria-selected', String(v === name));
    $(`${name}-view`).hidden = v !== name;
  }
}

async function showView(v, { load = true } = {}) {
  setTabs(v);
  if (v === 'now') await loadNow();
  else if (v === 'computer') await loadComputer();
  else if (!load) return;
  else if (!state.data && state.overview.first) await setPreset(state.overview.last - state.overview.first <= 6 * 3600 ? 'all' : '3600');
  else draw();
  if (v === 'timeline') loadStrip();
}

async function loadNow() {
  if (!state.overview.first) return;
  renderNow(await api('/api/now'));
}

const LEVEL_LABEL = { warning: 'Needs attention', tip: 'Tip', info: 'Good to know', good: 'Good' };

function stackBar(parts, total) {
  return `<div class="stackbar" role="img" aria-label="${esc(parts.map((p) => `${p.label} ${p.text}`).join(', '))}">${parts
    .map((p) => `<span class="${p.cls || ''}" style="flex:${Math.max(p.value, total * 0.004)};${p.colour ? `background:${p.colour}` : ''}"></span>`)
    .join('')}</div>
    <div class="bar-legend">${parts.filter((p) => !p.hideLegend).map((p) => `<span class="item">${p.colour ? `<span class="swatch" style="background:${p.colour}"></span>` : ''}${esc(p.label)} <span class="v">${esc(p.text)}</span></span>`).join('')}</div>`;
}

const VERDICT_CLASS = { fine: 'v-fine', ok: 'v-ok', heavy: 'v-heavy' };
let nowApps = new Map();

function describeLine(a) {
  if (a.what) return sentence(a.what);
  if (a.about) return a.about;
  return 'Not in Freezeframe’s list yet.';
}

function sourceNote(a) {
  if (a.source === 'ai') return a.ai_known ? 'Described by Claude.' : 'Described by Claude, which didn’t recognise it, so this comes from the app’s own details.';
  if (a.source === 'details') return 'From the app’s own details (who made it, its App Store category, how it runs).';
  return '';
}

function aboutFacts(a) {
  const f = [];
  if (a.developer) f.push(`Made by ${a.developer}`);
  if (a.category) f.push(`${sentence(a.category)} category`);
  if (a.app_store) f.push('from the App Store');
  if (a.version) f.push(`version ${a.version}`);
  if (a.menubar) f.push('lives in the menu bar');
  if (a.electron) f.push('built with Electron');
  return f.length ? sentence(f.join(', ')) + '.' : '';
}

function hourLine(a) {
  if (!a.hour) return 'Not busy enough in the last hour to be recorded much, which is a good sign.';
  return `Last hour: ${pct(a.hour.cpu_avg)} of the processor on average (peak ${pct(a.hour.cpu_max)}), up to ${bytes(a.hour.rss_max)} of memory.`;
}

function appCardHtml(a) {
  const v = a.verdict;
  return `<div class="t">${esc(a.app)}</div>
    <div>${esc(describeLine(a))}</div>
    ${v ? `<div class="tip-verdict"><span class="vdot ${VERDICT_CLASS[v.level]}"></span><b>${esc(v.label)}.</b> ${esc(v.text)}</div>` : ''}
    <div class="muted">${esc(hourLine(a))}</div>
    ${sourceNote(a) ? `<div class="muted">${esc(sourceNote(a))}</div>` : ''}`;
}

function aiBarHtml(n) {
  const ai = n.ai;
  const o = state.overview;
  if (!ai || o.site || o.demo) return '';
  const unknown = n.apps.filter((a) => !a.source || a.source === 'details').length;
  if (ai.enabled) {
    if (ai.error) return `<p class="ai-bar error">${esc(ai.error)} <button type="button" class="quiet" id="ai-change">Change key</button></p>`;
    if (ai.busy) return `<p class="ai-bar">Asking Claude to describe ${unknown} unfamiliar app${unknown === 1 ? '' : 's'}…</p>`;
    return '';
  }
  if (!unknown) return '';
  return `<div class="ai-bar">
    <p>${unknown} app${unknown === 1 ? ' isn’t' : 's aren’t'} in Freezeframe’s list. ${ai.installed ? '' : ''}<button type="button" class="quiet" id="ai-open">Describe them with Claude</button></p>
    <form id="ai-form" hidden>
      <label for="ai-key">Anthropic API key</label>
      <input id="ai-key" type="password" autocomplete="off" placeholder="sk-ant-…" />
      <button type="submit" class="primary">Turn on</button>
      <p class="fine-print">Get a key at console.anthropic.com. Freezeframe sends only app names, developers and categories, once per app. The key is saved on this computer, and you can remove it any time with <code>freezeframe ai --off</code>.
      ${ai.installed ? '' : ' This also needs the AI add-on: <code>pip install "freezeframe[ai]"</code>.'}</p>
      <p class="ai-error" id="ai-error" hidden></p>
    </form>
  </div>`;
}

function renderNow(n) {
  $('tip').hidden = true;
  const s = n.sample;
  const m = n.machine || {};
  if (!s) {
    $('now-view').innerHTML = '';
    return;
  }
  const ncpu = m.cores || state.overview.recorder?.cpus || 8;
  const apps = n.apps;
  nowApps = new Map(apps.map((a) => [a.app, a]));
  const byMem = [...apps].sort((a, b) => b.rss - a.rss).slice(0, 6);
  assignSlots(byMem.map((a) => a.app));
  const used = s.mem_total - s.mem_available;
  const pressure = { 1: 'normal', 2: 'elevated', 4: 'critical' }[s.pressure];
  const readouts = [
    { label: 'Processor', value: `${Math.round(s.cpu)}%`, sub: `busy, across ${ncpu} cores${m.cpu ? `, ${m.cpu}` : ''}` },
    { label: 'Memory', value: bytes(used), sub: `of ${bytes(s.mem_total)}${pressure ? `, pressure ${pressure}` : ''}` },
  ];
  if (m.disk_total) {
    const full = 100 * (1 - m.disk_free / m.disk_total);
    readouts.push({ label: 'Disk', value: `${Math.round(full)}%`, sub: `full, ${bytes(m.disk_free)} free` });
  }
  if (m.battery) {
    readouts.push({ label: 'Battery', value: `${m.battery.percent}%`, sub: m.battery.health ? `health ${m.battery.health}%${m.battery.cycles ? `, ${m.battery.cycles} cycles` : ''}` : m.battery.plugged ? 'plugged in' : 'on battery' });
  }
  if (m.uptime) readouts.push({ label: 'Running for', value: m.uptime >= 172800 ? `${(m.uptime / 86400).toFixed(1)} d` : duration(m.uptime), sub: 'since the last restart' });
  if (s.swap_used) readouts.push({ label: 'Swap', value: bytes(s.swap_used), sub: 'memory parked on disk' });

  const memParts = byMem.map((a) => ({ label: a.app, value: a.rss, text: bytes(a.rss), colour: colourFor(a.app) }));
  const memApps = memParts.reduce((t, p) => t + p.value, 0);
  memParts.push({ label: 'System and other apps', value: Math.max(0, used - memApps), text: bytes(Math.max(0, used - memApps)), colour: css('--other') });
  memParts.push({ label: 'Free', value: s.mem_available, text: bytes(s.mem_available), cls: 'free' });

  const byCpu = [...apps].filter((a) => a.cpu >= 1).sort((a, b) => b.cpu - a.cpu).slice(0, 6);
  assignSlots(byCpu.map((a) => a.app));
  const cpuParts = byCpu.map((a) => ({ label: a.app, value: a.cpu / ncpu, text: pct(a.cpu, ncpu), colour: colourFor(a.app) }));
  const cpuApps = cpuParts.reduce((t, p) => t + p.value, 0);
  cpuParts.push({ label: 'Everything else', value: Math.max(0, s.cpu - cpuApps), text: `${Math.max(0, Math.round(s.cpu - cpuApps))}%`, colour: css('--other') });
  cpuParts.push({ label: 'Idle', value: Math.max(0, 100 - s.cpu), text: `${Math.round(100 - s.cpu)}%`, cls: 'free' });

  const open = new Set([...document.querySelectorAll('.running details[open]')].map((d) => d.dataset.app));
  $('now-view').innerHTML = `
    <div class="readouts">${readouts.map((r) => `<div class="readout"><div class="label">${esc(r.label)}</div><div class="value">${esc(r.value)}</div><div class="sub">${esc(r.sub)}</div></div>`).join('')}</div>
    <div class="breakdown"><h3>Memory by app</h3>${stackBar(memParts, s.mem_total)}</div>
    <div class="breakdown"><h3>Processor by app <span class="h-sub">share of the whole computer</span></h3>${stackBar(cpuParts, 100)}</div>
    <section class="now"><h2>Worth knowing</h2>
      <ul class="insights">${n.insights.map((i) => `<li class="insight"><span class="icon lvl-${i.level}" aria-hidden="true"></span>
        <div><span class="title">${esc(i.title)}</span><span class="level">${LEVEL_LABEL[i.level]}</span></div>
        <div class="body">${esc(i.body)}</div></li>`).join('')}</ul>
    </section>
    <section class="now"><h2>What's running</h2>
      <p class="section-hint">Point at an app to see what it is and whether it’s fine to leave open. Click it for more.</p>
      ${aiBarHtml(n)}
      <div class="running">
        <div class="head"><span>App</span><span class="num">Memory</span><span class="num">Processor</span></div>
        ${apps.map((a) => `<details data-app="${esc(a.app)}"${open.has(a.app) ? ' open' : ''}><summary>
          <span><span class="name"><span class="swatch" style="background:${state.appSlots.has(a.app) ? colourFor(a.app) : 'transparent'}"></span>${esc(a.app)}${a.nproc > 1 ? ` <span class="procs">${a.nproc} processes</span>` : ''}</span>
          <span class="what">${esc(describeLine(a))}</span>
          ${a.verdict ? `<span class="verdict ${VERDICT_CLASS[a.verdict.level]}"><span class="vdot"></span>${esc(a.verdict.label)}</span>` : ''}</span>
          <span class="num">${bytes(a.rss)}</span><span class="num">${pct(a.cpu, ncpu)}</span></summary>
          <div class="more">
            ${a.verdict ? `<p><b>${esc(a.verdict.label)}:</b> ${esc(a.verdict.text)}</p>` : ''}
            ${a.stay_open ? `<p><b>Claude’s take:</b> ${esc(a.stay_open)}</p>` : ''}
            ${a.normal ? `<p><b>What's normal:</b> ${esc(a.normal)}</p>` : ''}
            ${a.tip ? `<p><b>If it's causing trouble:</b> ${esc(a.tip)}</p>` : ''}
            ${a.what && a.about ? `<p><b>About it:</b> ${esc(a.about)}</p>` : ''}
            ${aboutFacts(a) ? `<p class="facts">${esc(aboutFacts(a))}</p>` : ''}
            <p class="facts">${esc(hourLine(a))}${a.top_name && a.top_name !== a.app ? ` Its busiest process is ${esc(a.top_name)}.` : ''}</p>
            ${sourceNote(a) ? `<p class="facts">${esc(sourceNote(a))}</p>` : ''}
          </div>
        </details>`).join('')}
      </div>
    </section>`;
  wireNow();
}

function wireNow() {
  for (const sm of document.querySelectorAll('.running summary')) {
    const app = sm.parentElement.dataset.app;
    sm.addEventListener('pointermove', (ev) => {
      if (ev.pointerType !== 'mouse' || sm.parentElement.open) return;
      const a = nowApps.get(app);
      if (!a) return;
      tip.innerHTML = appCardHtml(a);
      tip.hidden = false;
      placeTip(ev);
    });
    sm.addEventListener('pointerleave', () => {
      tip.hidden = true;
    });
    sm.addEventListener('click', () => {
      tip.hidden = true;
    });
  }
  $('ai-open')?.addEventListener('click', () => {
    $('ai-form').hidden = false;
    $('ai-key').focus();
  });
  $('ai-change')?.addEventListener('click', async () => {
    await api('/api/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ forget: true }) });
    loadNow();
  });
  $('ai-form')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try {
      await api('/api/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: $('ai-key').value }) });
      await loadNow();
    } catch (e) {
      $('ai-error').textContent = e.message;
      $('ai-error').hidden = false;
    }
  });
}

// --------------------------------------------------------- this computer

let storagePoll = null;

async function loadComputer() {
  const [hw, storage] = await Promise.all([api('/api/hardware'), api('/api/storage')]);
  renderComputer(hw, storage);
}

function renderComputer(hw, storage) {
  const o = state.overview;
  const canAct = !o.site && !o.demo;
  const level = (l) => (l ? `<span class="icon lvl-${l === 'good' ? 'good' : 'warning'}" aria-hidden="true"></span>` : '');
  const sections = hw.sections.map((sec) => `<section class="hw-card"><h3>${esc(sec.title)}</h3><dl>${sec.items.map((i) => `
      <div class="hw-item"><dt>${esc(i.label)}</dt><dd><span class="hw-value">${level(i.level)}${esc(i.value)}</span>${i.note ? `<span class="hw-note">${esc(i.note)}</span>` : ''}</dd></div>`).join('')}</dl></section>`).join('');
  $('computer-view').innerHTML = `
    <div class="computer-head"><h2>${esc(hw.model)}</h2><p>${esc(hw.os)}${hw.uptime ? `, running for ${hw.uptime >= 172800 ? `${(hw.uptime / 86400).toFixed(1)} days` : duration(hw.uptime)} since the last restart` : ''}</p></div>
    ${hw.error ? `<p class="ai-bar error">${esc(hw.error)}</p>` : ''}
    <div class="hw-grid">${sections}</div>
    <section class="now" id="storage">${storageHtml(storage, canAct)}</section>`;
  wireStorage(canAct);
}

function storageHtml(st, canAct) {
  const vols = (st.volumes || []).map((v) => {
    const used = v.used / v.total;
    return `<div class="volume"><div class="volume-head"><b>${esc(v.name)}</b><span>${bytes(v.free)} free of ${bytes(v.total)}</span></div>
      <div class="stackbar" role="img" aria-label="${Math.round(used * 100)}% full"><span style="flex:${used};background:${used >= 0.9 ? css('--critical') : used >= 0.8 ? css('--warning') : css('--s1')}"></span><span class="free" style="flex:${1 - used}"></span></div></div>`;
  }).join('');
  let body;
  if (st.state === 'idle') {
    body = `<p class="section-hint">Freezeframe can add up the usual space hogs: apps and games, caches, videos, developer files. It only measures; nothing is moved or deleted.</p>
      <p><button type="button" class="primary" id="scan">Find what’s using space</button></p>`;
  } else {
    const items = st.items.filter((i) => i.size >= 50 * MB || i.error);
    const max = Math.max(...items.map((i) => i.size), 1);
    body = `${st.state === 'scanning' ? '<p class="section-hint">Adding up folder sizes… this takes up to 30 seconds.</p>' : ''}
      <ul class="space">${items.map((i) => `<li>
        <div class="space-head"><b>${esc(i.label)}</b><span class="num">${i.complete ? '' : 'at least '}${bytes(i.size)}</span></div>
        <div class="space-bar"><span style="width:${(100 * i.size) / max}%"></span></div>
        <p>${esc(i.note)}${i.error ? ` <em>${esc(i.error)}</em>` : ''}</p>
        ${i.children?.length ? `<ul class="kids">${i.children.slice(0, 5).map((c) => `<li><span>${esc(c.name)}</span><span class="num">${bytes(c.size)}</span>${canAct ? `<button type="button" class="quiet" data-reveal="${esc(c.path)}">Show</button>` : ''}</li>`).join('')}</ul>` : ''}
        ${i.how && i.size >= 500 * MB ? `<p class="how"><b>To free space:</b> ${esc(i.how)}</p>` : ''}
        ${canAct && !i.error ? `<button type="button" class="quiet" data-reveal="${esc(i.path)}">Show in ${navigator.platform.startsWith('Win') ? 'Explorer' : navigator.platform.startsWith('Mac') ? 'Finder' : 'files'}</button>` : ''}
      </li>`).join('')}</ul>
      ${st.state === 'done' && !st.private && canAct ? `<p class="section-hint">Downloads, Desktop, Documents and the Trash weren’t included, because your computer asks permission before an app looks inside them.
        <button type="button" class="quiet" id="scan-private">Include them too</button></p>` : ''}`;
  }
  return `<h2>Disk space</h2>${vols}${body}`;
}

function wireStorage(canAct) {
  const start = async (priv) => {
    const st = await api('/api/storage', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ private: priv }) });
    $('storage').innerHTML = storageHtml(st, canAct);
    pollStorage(canAct);
  };
  $('scan')?.addEventListener('click', () => start(false));
  $('scan-private')?.addEventListener('click', () => start(true));
  for (const b of document.querySelectorAll('[data-reveal]')) {
    b.addEventListener('click', () => api('/api/reveal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: b.dataset.reveal }) }));
  }
}

function pollStorage(canAct) {
  clearTimeout(storagePoll);
  storagePoll = setTimeout(async () => {
    if (currentView !== 'computer') return;
    const st = await api('/api/storage');
    $('storage').innerHTML = storageHtml(st, canAct);
    wireStorage(canAct);
    if (st.state === 'scanning') pollStorage(canAct);
  }, 1000);
}

// --------------------------------------------------------------- explain

async function selectIncident(id, moveView = true) {
  state.selected = { kind: 'incident', id };
  state.ask = null;
  renderIncidents();
  if (currentView !== 'timeline') {
    setTabs('timeline');
    loadStrip();
  }
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
  $('explain').innerHTML = explainHtml(d, event, moment);
}

function explainHtml(d, event, moment, sub = false) {
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
  const H = sub ? 'h3' : 'h2';
  return `
    ${sub ? '' : `<p class="when">${whenLine}</p>`}
    <${H}>${esc(top.headline)}</${H}>
    ${top.score > 0 ? confidenceHtml(d.confidence) : ''}
    <p class="lead">${esc(top.explanation)}</p>
    ${top.evidence.length ? `<h3>What the recording shows</h3>${list(top.evidence)}` : ''}
    ${top.actions.length ? `<h3>What you can do</h3>${list(top.actions)}` : ''}
    ${others.length ? `<h3>Also noticed</h3><ul class="also">${others.map((f) => `<li>${esc(f.headline)} <span class="tag">${f.score >= 0.5 ? 'likely contributed' : 'possibly related'}</span></li>`).join('')}</ul>` : ''}
    ${apps.length ? `<h3>Busiest apps in the 2 minutes before</h3>
      <table class="apps"><thead><tr><th scope="col">App</th><th scope="col" class="num">Memory</th><th scope="col" class="num">Change</th><th scope="col" class="num">Processor</th></tr></thead>
      <tbody>${apps.map((a) => `<tr>
        <td><span class="app-name"><span class="swatch" style="background:${colourFor(a.app)}"></span>${esc(a.app)}</span>${a.what ? `<span class="what">${esc(sentence(a.what))}</span>` : ''}</td>
        <td class="num">${bytes(a.rss)}</td>
        <td class="num">${Math.abs(a.growth) >= 100 * MB ? (a.growth > 0 ? '+' : '−') + bytes(Math.abs(a.growth)) : ''}</td>
        <td class="num">${pct(a.cpu_avg)}</td></tr>`).join('')}</tbody></table>` : ''}
    ${d.related.length ? `<h3>Around the same time</h3><ul class="related">${d.related.slice(0, 8).map((r) => `<li><span class="t">${esc(clock(r.ts))}</span><span>${esc(r.title)}</span></li>`).join('')}</ul>` : ''}
    ${report ? `<h3>The system's own report</h3><p class="report-path">${esc(report)}</p>` : ''}`;
}

// ---------------------------------------------------------- what happened

const LINE_ICON = { problem: 'warning', busy: 'warning', note: 'info', ok: 'good' };
const EXAMPLES = ['the last 10 minutes', '20 minutes ago', 'around 3pm', 'yesterday evening'];

async function ask(q, { fromHash = false } = {}) {
  q = q.trim();
  if (!q) return;
  $('ask-input').value = q;
  if (!state.overview.first) return;
  setTabs('timeline');
  loadStrip();
  let r;
  try {
    r = await api(`/api/what?q=${encodeURIComponent(q)}`);
  } catch (e) {
    if (!state.data) await setPreset('3600');
    $('explain').innerHTML = `<h2>Freezeframe didn’t understand “${esc(q)}”</h2>
      <p class="lead">Try a time the way you’d say it:</p>
      <p class="examples">${EXAMPLES.map((x) => `<button type="button" class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join('')}</p>`;
    wireExamples();
    return;
  }
  showAnswer(r);
  if (!fromHash) history.replaceState(null, '', `#what=${encodeURIComponent(q)}`);
}

async function showAnswer(r) {
  state.ask = { start: r.start, end: r.end };
  state.selected = r.focus?.incident ? { kind: 'incident', id: r.focus.incident } : r.focus?.at ? { kind: 'moment', ts: r.focus.at } : null;
  renderIncidents();
  const span = Math.max(300, r.end - r.start);
  await setRange(r.start - span * 0.25, r.end + span * 0.25);
  const line = (l) => {
    const t = l.incident ? `data-incident="${l.incident}"` : l.ts ? `data-ts="${l.ts}"` : '';
    return `<li class="answer-line"><span class="icon lvl-${LINE_ICON[l.level]}" aria-hidden="true"></span>
      <span>${t ? `<button type="button" class="linkish" ${t}>${esc(l.text)}</button>` : esc(l.text)}</span></li>`;
  };
  const appsHtml = r.apps.length ? `<h3>Busiest apps then</h3><table class="apps"><thead><tr><th scope="col">App</th><th scope="col" class="num">Processor</th><th scope="col" class="num">Peak</th><th scope="col" class="num">Memory</th></tr></thead>
    <tbody>${r.apps.map((a) => `<tr><td><span class="app-name"><span class="swatch" style="background:${colourFor(a.app)}"></span>${esc(a.app)}</span></td>
      <td class="num">${a.cpu_pct}%</td><td class="num">${a.cpu_peak_pct}%</td><td class="num">${bytes(a.rss_max)}</td></tr>`).join('')}</tbody></table>` : '';
  $('explain').innerHTML = `
    <p class="when">You asked about ${esc(r.label)}: ${esc(when(r.start))} to ${esc(day(r.end) === day(r.start) ? clock(r.end) : when(r.end))}</p>
    <h2>${esc(r.headline)}</h2>
    <ul class="answer">${r.lines.map(line).join('')}</ul>
    ${appsHtml}
    <div id="answer-cause"></div>`;
  for (const b of $('explain').querySelectorAll('[data-incident]')) b.addEventListener('click', () => selectIncident(Number(b.dataset.incident)));
  for (const b of $('explain').querySelectorAll('[data-ts]')) b.addEventListener('click', () => selectMoment(Number(b.dataset.ts)));
  if (r.focus) {
    const q = r.focus.incident ? `id=${r.focus.incident}` : `at=${r.focus.at}`;
    const d = await api(`/api/diagnose?${q}`);
    $('answer-cause').innerHTML = `<div class="cause"><p class="when">Most likely cause</p>${explainHtml(d.diagnosis, d.event, r.focus.at, true)}</div>`;
  }
}

function wireExamples() {
  for (const b of document.querySelectorAll('.chip[data-q]')) b.addEventListener('click', () => ask(b.dataset.q));
}

$('ask-form').addEventListener('submit', (ev) => {
  ev.preventDefault();
  ask($('ask-input').value);
});

function renderWelcome() {
  $('explain').innerHTML = `<div class="welcome">
    <h2>Nothing recorded yet</h2>
    <p class="lead">Freezeframe needs to be running when a freeze happens. Start it in the background, and it will start again every time you log in:</p>
    <p><code>freezeframe install</code></p>
    <p>Then come back here, or run <code>freezeframe why</code> in a terminal, the next time something goes wrong. To see what it can do right now, run <code>freezeframe open --demo</code>.</p>
  </div>`;
}

// ---------------------------------------------------------------- drawing

const MAIN_LANE = 'cpu-apps';
const canvasOf = (lane) => document.querySelector(`.lane[data-lane="${lane}"] canvas`);

function lanesWidth() {
  return canvasOf(MAIN_LANE).getBoundingClientRect().width;
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
  if (state.ask) {
    // The stretch the person asked about.
    const x0 = xOf(state.ask.start, w);
    const x1 = xOf(state.ask.end, w);
    g.fillStyle = css('--ask');
    g.fillRect(x0, 0, Math.max(2, x1 - x0), h);
  }
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

function stacked(lane, totalOf, valueOf, maxValue, label, apps = state.shownApps) {
  const { g, w, h } = prep(lane);
  const top = 6;
  const plotH = h - top - 2;
  verticalGrid(g, w, h);
  sleepBands(g, w, h);
  const series = state.data.series;
  const per = state.data.app_series;
  const byApp = apps.map((a) => new Map((per[a] || []).map((p) => [p.t, p])));
  const y = (v) => top + plotH - (Math.min(v, maxValue) / maxValue) * plotH;
  const surface = css('--surface');
  const allLayers = [];

  for (const seg of segments(series)) {
    const layers = seg.map((p) => {
      const vals = byApp.map((m) => valueOf(m.get(p.t)));
      const total = totalOf(p);
      const sum = vals.reduce((a, b) => a + b, 0);
      return { t: p.t, vals: [...vals, Math.max(0, total - sum)], total: Math.max(total, sum) };
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
    allLayers.push(...layers);
  }
  incidentBands(g, w, h);
  axisLabel(g, label);
  return { g, w, h, y, layers: allLayers, apps };
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
  const ncpu = ncores();
  drawEvents();
  drawCpuLanes(ncpu);
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

/** The smallest of 10/25/50/100% that fits, so small spikes are visible without exaggerating them. */
function niceScale(peak) {
  return [10, 25, 50, 100].find((m) => peak * 1.08 <= m) || 100;
}

function drawCpuLanes(ncpu) {
  const d = state.data;
  const kindAt = new Map((d.kind_series || []).map((k) => [k.t, k]));
  const kinds = d.kinds || {};
  const yours = state.shownApps.filter((a) => (kinds[a] || 'app') === 'app');
  const bg = state.shownApps.filter((a) => (kinds[a] || 'app') !== 'app');
  const appsTotal = (p) => Math.min(p.cpu, kindAt.get(p.t)?.apps ?? p.cpu);
  const bgTotal = (p) => Math.max(0, p.cpu - appsTotal(p));
  const peak = Math.max(...d.series.map((p) => Math.max(appsTotal(p), bgTotal(p))), 1);
  const scale = niceScale(peak);
  const val = (a) => (a ? a.cpu / ncpu : 0);
  const one = stacked('cpu-apps', appsTotal, val, scale, `${scale}%`, yours);
  spikeLabels(one, 'apps', yours, kindAt, kinds);
  const two = stacked('cpu-bg', bgTotal, val, scale, `${scale}%`, bg);
  spikeLabels(two, 'background', bg, kindAt, kinds);
}

/** Labels the two or three biggest spikes in a CPU lane with their size and the app behind them. */
function spikeLabels(lane, kind, apps, kindAt, kinds) {
  const { g, w, y, layers } = lane;
  const minGap = w * 0.12;
  const picked = [];
  const sorted = [...layers].sort((a, b) => b.total - a.total);
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)].total : 0;
  for (const l of sorted) {
    if (picked.length >= 3 || l.total < Math.max(12, median * 2.5)) break;
    const x = xOf(l.t, w);
    if (picked.some((p) => Math.abs(p.x - x) < minGap)) continue;
    let who = null;
    let best = -1;
    apps.forEach((a, i) => {
      if (l.vals[i] > best) {
        best = l.vals[i];
        who = a;
      }
    });
    const other = l.vals[l.vals.length - 1];
    if (other > best) {
      const top = kindAt.get(l.t)?.top;
      who = top && ((kinds[top] || 'app') === 'app') === (kind === 'apps') ? top : kind === 'apps' ? 'other apps' : 'the system';
    }
    picked.push({ x, l, who });
  }
  g.font = `600 12px ${css('--sans')}`;
  for (const { x, l, who } of picked) {
    const text = `${Math.round(l.total)}% ${who}`;
    const tw = g.measureText(text).width;
    const tx = Math.min(Math.max(4, x - tw / 2), w - tw - 4);
    const ty = Math.max(14, y(l.total) - 6);
    g.fillStyle = css('--surface');
    g.globalAlpha = 0.9;
    g.fillRect(tx - 3, ty - 11, tw + 6, 15);
    g.globalAlpha = 1;
    g.fillStyle = css('--ink');
    g.fillText(text, tx, ty);
  }
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
  const r = canvasOf(MAIN_LANE).getBoundingClientRect();
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
    const w = canvasOf(MAIN_LANE).getBoundingClientRect().width;
    const near = state.data.events.filter((e) => e.kind !== 'sleep' && Math.abs(xOf(e.ts, w) - x) <= 8);
    if (near.length) html = near.slice(0, 4).map((e) => `<div class="t">${esc(clock(e.ts, true))}</div><div>${esc(e.title)}</div>`).join('');
  }
  if (!html) {
    const p = nearestBucket(t);
    if (!p) {
      tip.hidden = true;
      return;
    }
    const per = state.data.app_series;
    const k = (state.data.kind_series || []).find((q) => q.t === p.t);
    const appsAt = state.shownApps
      .map((a) => ({ a, v: (per[a] || []).find((q) => q.t === p.t) }))
      .filter((x) => x.v)
      .sort((m, n) => n.v.cpu - m.v.cpu)
      .slice(0, 4);
    const used = p.mem_total - p.mem_available;
    html = `<div class="t">${esc(when(p.t, true))}</div>
      <div class="row"><span>Processor</span><span>${Math.round(p.cpu)}% busy</span></div>
      ${k ? `<div class="row"><span>Your apps</span><span>${Math.round(Math.min(p.cpu, k.apps))}%</span></div><div class="row"><span>Background and system</span><span>${Math.round(Math.max(0, p.cpu - k.apps))}%</span></div>` : ''}
      ${appsAt.map(({ a, v }) => `<div class="row"><span><span class="sw" style="background:${colourFor(a)}"></span>${esc(a)}</span><span>${pct(v.cpu)}, ${bytes(v.rss)}</span></div>`).join('')}
      <div class="row"><span>Memory in use</span><span>${bytes(used)} of ${bytes(p.mem_total)}</span></div>
      ${p.pressure >= 2 ? `<div class="row"><span>Memory pressure</span><span>${p.pressure >= 4 ? 'critical' : 'warning'}</span></div>` : ''}
      <div class="row"><span>Swapping</span><span>${rate((p.swap_in || 0) + (p.swap_out || 0))}</span></div>
      <div class="row"><span>Disk</span><span>${rate(Math.max(p.disk_read || 0, p.disk_write || 0))}</span></div>
      ${p.thermal ? `<div class="row"><span>Heat</span><span>${p.thermal >= 2 ? 'throttling hard' : 'warm, slowing down'}</span></div>` : ''}`;
  }
  tip.innerHTML = html;
  tip.hidden = false;
  placeTip(ev);
}

function placeTip(ev) {
  const tx = ev.clientX + 16 + tip.offsetWidth > window.innerWidth ? ev.clientX - tip.offsetWidth - 16 : ev.clientX + 16;
  tip.style.left = `${Math.max(8, tx)}px`;
  tip.style.top = `${Math.max(8, Math.min(ev.clientY + 12, window.innerHeight - tip.offsetHeight - 8))}px`;
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
  try {
    lanesEl.setPointerCapture(ev.pointerId);
  } catch {
    // Not every pointer can be captured (e.g. synthetic events); dragging still works without it.
  }
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
  const w = canvasOf(MAIN_LANE).getBoundingClientRect().width;
  const hit = state.data.events.find((e) => INCIDENT_KINDS.has(e.kind) && Math.abs(xOf(e.ts, w) - d.x0) <= 6);
  if (hit && state.overview.incidents.some((i) => i.id === hit.id)) selectIncident(hit.id, false);
  else selectMoment(t);
});

$('tab-now').addEventListener('click', () => showView('now'));
$('tab-timeline').addEventListener('click', () => showView('timeline'));
$('tab-computer').addEventListener('click', () => showView('computer'));
for (const b of document.querySelectorAll('#ranges button')) b.addEventListener('click', () => setPreset(b.dataset.range));

function pan(fraction) {
  const span = state.end - state.start;
  return setRange(state.start + span * fraction, state.end + span * fraction);
}

function zoom(factor, at = (state.start + state.end) / 2) {
  const span = Math.max(120, (state.end - state.start) * factor);
  const left = (at - state.start) / (state.end - state.start);
  return setRange(at - span * left, at + span * (1 - left));
}

function jumpProblem(dir) {
  const mid = (state.start + state.end) / 2;
  const list = [...state.overview.incidents].sort((a, b) => a.ts - b.ts);
  const next = dir > 0 ? list.find((e) => e.ts > mid + 1) : [...list].reverse().find((e) => e.ts < mid - 1);
  if (next) selectIncident(next.id);
  else flash(dir > 0 ? 'No later problems.' : 'No earlier problems.');
}

function flash(text) {
  $('range-label').textContent = text;
  setTimeout(renderRangeLabel, 1600);
}

$('pan-back').addEventListener('click', () => pan(-0.5));
$('pan-fwd').addEventListener('click', () => pan(0.5));
$('pan-now').addEventListener('click', () => {
  const span = state.end - state.start;
  setRange(state.overview.last - span, state.overview.last, state.rangeKey);
});
$('zoom-in').addEventListener('click', () => zoom(0.5));
$('zoom-out').addEventListener('click', () => zoom(2));
$('prev-problem').addEventListener('click', () => jumpProblem(-1));
$('next-problem').addEventListener('click', () => jumpProblem(1));

document.addEventListener('keydown', (ev) => {
  if (currentView !== 'timeline' || !state.data || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (ev.target.closest('input, textarea, select')) return;
  const keys = {
    ArrowLeft: () => pan(ev.shiftKey ? -1 : -0.25),
    ArrowRight: () => pan(ev.shiftKey ? 1 : 0.25),
    '+': () => zoom(0.5),
    '=': () => zoom(0.5),
    '-': () => zoom(2),
    '[': () => jumpProblem(-1),
    ']': () => jumpProblem(1),
  };
  if (keys[ev.key]) {
    ev.preventDefault();
    keys[ev.key]();
  }
});

// Sideways scrolling (trackpad swipe or shift+wheel) moves in time; pinch or ctrl+wheel zooms.
let wheelTimer = null;
let wheelDelta = { x: 0, zoom: 1, at: 0 };
lanesEl.addEventListener('wheel', (ev) => {
  if (!state.data) return;
  const sideways = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) || ev.shiftKey;
  if (!sideways && !ev.ctrlKey) return; // plain vertical scrolling still scrolls the page
  ev.preventDefault();
  const w = lanesWidth();
  if (ev.ctrlKey) {
    wheelDelta.zoom *= Math.exp(ev.deltaY * 0.01);
    wheelDelta.at = eventTime(ev).t;
  } else {
    wheelDelta.x += (ev.shiftKey && !ev.deltaX ? ev.deltaY : ev.deltaX) / w;
  }
  clearTimeout(wheelTimer);
  wheelTimer = setTimeout(() => {
    const d = wheelDelta;
    wheelDelta = { x: 0, zoom: 1, at: 0 };
    if (d.zoom !== 1) zoom(d.zoom, d.at);
    else if (d.x) pan(d.x);
  }, 60);
}, { passive: false });

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
  resizeTimer = setTimeout(() => {
    draw();
    drawStrip();
  }, 120);
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  draw();
  renderLegend();
});

async function refreshLive() {
  if (!state.overview?.live) return;
  await loadOverview();
  if (currentView === 'now') {
    if (!document.querySelector('.ai-bar form:not([hidden])')) await loadNow(); // don't wipe a half-typed key
    return;
  }
  if (currentView !== 'timeline') return;
  const followed = state.overview.last - state.end < 60;
  const span = state.end - state.start;
  if (followed && !drag) await setRange(state.overview.last - span, state.overview.last, state.rangeKey);
}

async function main() {
  await loadOverview();
  const o = state.overview;
  if (!o.first) {
    $('now-view').hidden = true;
    $('timeline-view').hidden = false;
    renderWelcome();
    return;
  }
  await route();
  setInterval(refreshLive, 5000);
  window.addEventListener('hashchange', route);
}

/** Links from the menu-bar icon and elsewhere: #incident=12, #what=20 minutes ago, #ask, #view=computer */
async function route() {
  const o = state.overview;
  const params = new URLSearchParams(location.hash.slice(1));
  const incident = Number(params.get('incident'));
  if (incident && !o.incidents.some((i) => i.id === incident)) await loadOverview();
  if (incident && state.overview.incidents.some((i) => i.id === incident)) {
    await selectIncident(incident);
  } else if (params.get('what')) {
    await ask(params.get('what'), { fromHash: true });
  } else if (params.has('ask')) {
    if (!state.data) await showView('timeline');
    $('ask-input').focus();
  } else if (['timeline', 'computer', 'now'].includes(params.get('view'))) {
    await showView(params.get('view'));
  } else if (!currentView || !state.data) {
    await showView('now');
  }
}

main().catch((e) => {
  $('explain').innerHTML = `<h2>Couldn't load the recording</h2><p class="lead">${esc(e.message)}</p>`;
});
