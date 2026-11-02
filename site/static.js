// Stands in for the local Freezeframe server on the public demo website.
// It answers the dashboard's API calls from exported demo data, shifted so the
// recording ends a minute ago, and supports "Something went wrong" in memory.
(function () {
  const D = window.FREEZEFRAME_DEMO;
  const shift = Date.now() / 1000 - 60 - D.overview.last;
  const TIME_KEYS = new Set(['ts', 'end_ts', 't', 'first', 'last', 'start', 'end', 'started', 'at']);
  const added = []; // incidents created with "Something went wrong"
  const addedDiagnoses = {};
  let nextId = 100000;

  function shifted(value) {
    if (Array.isArray(value)) return value.map(shifted);
    if (value && typeof value === 'object') {
      const out = {};
      for (const [k, v] of Object.entries(value)) out[k] = TIME_KEYS.has(k) && typeof v === 'number' ? v + shift : shifted(v);
      return out;
    }
    return value;
  }

  function nearestMoment(t) {
    const grid = D.moments;
    let best = grid[0];
    for (const m of grid) if (Math.abs(m.at - t) < Math.abs(best.at - t)) best = m;
    return best.diagnosis;
  }

  const within = (lo, hi) => (p) => p.t >= lo && p.t <= hi;

  // A small version of Freezeframe's time parser (when.py) for the demo.
  function parseWhen(text, now) {
    let t = text.trim().toLowerCase().replace(/^(what happened|at|around|about|~)\s*/g, '').replace(/\?$/, '');
    const units = { s: 1, sec: 1, second: 1, m: 60, min: 60, minute: 60, h: 3600, hr: 3600, hour: 3600 };
    const num = (x) => (x === 'a' || x === 'an' || x === 'one' ? 1 : x === 'a few' || x === 'few' ? 3 : Number(x));
    const clockOf = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (['now', 'just now', 'right now'].includes(t)) return { start: now - 300, end: now, label: 'the last 5 minutes' };
    let m = t.match(/^(?:the )?(?:last|past) ?(\d+|a|an|one|a few)? ?(second|sec|minute|min|hour|hr)s?$/);
    if (m) return { start: now - num(m[1] || 1) * units[m[2]], end: now, label: `the last ${m[1] ? `${m[1]} ` : ''}${m[2]}${m[1] && num(m[1]) !== 1 ? 's' : ''}` };
    m = t.match(/^(\d+(?:\.\d+)?|a|an|one|a few|few) ?(s|sec|second|m|min|minute|h|hr|hour)s?(?: ago)?$/);
    if (m) {
      const ago = num(m[1]) * units[m[2]];
      const half = Math.max(120, Math.min(ago * 0.15, 1800));
      return { start: now - ago - half, end: Math.min(now, now - ago + half), label: `about ${clockOf(now - ago)} (${t.includes('ago') ? t : `${t} ago`})` };
    }
    m = t.match(/^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$/);
    if (m) {
      let h = Number(m[1]);
      const min = Number(m[2] || 0);
      const ap = m[3];
      if (ap?.startsWith('p') && h < 12) h += 12;
      if (ap?.startsWith('a') && h === 12) h = 0;
      const cands = !ap && h >= 1 && h <= 12 ? [h % 12, (h % 12) + 12] : [h];
      let best = null;
      for (const back of [0, 1]) {
        for (const hh of cands) {
          const d = new Date(now * 1000);
          d.setDate(d.getDate() - back);
          d.setHours(hh, min, 0, 0);
          const ts = d.getTime() / 1000;
          if (ts <= now + 60 && (best === null || ts > best)) best = ts;
        }
      }
      const half = m[2] ? 180 : 900;
      return { start: best - half, end: Math.min(now, best + half), label: `${m[2] ? '' : 'around '}${clockOf(best)}` };
    }
    return null;
  }

  // A small version of what.py: incidents first, then strain, otherwise "nothing unusual".
  function whatHappened(lo, hi, label) {
    const T = D.timeline;
    const series = T.series.filter(within(lo, hi));
    const kinds = new Map(T.kind_series.map((k) => [k.t, k]));
    const incidents = [...T.events, ...added].filter((e) => e.incident && (e.end_ts ?? e.ts) >= lo && e.ts <= hi).sort((a, b) => b.severity - a.severity);
    const at = (ts) => new Date((ts + shift) * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const lines = incidents.map((e) => ({ level: 'problem', text: `${at(e.ts)}: ${e.title}`, ts: e.ts, incident: e.id }));
    const base = { start: lo, end: hi, label, apps: [], stats: {} };
    if (!series.length) {
      lines.push({ level: 'note', text: 'Freezeframe wasn’t recording then (the demo covers the last four hours).' });
      return { ...base, verdict: incidents.length ? 'problem' : 'no-data', headline: incidents[0]?.title || "Freezeframe wasn't recording then", lines, focus: incidents[0] ? { incident: incidents[0].id } : null };
    }
    const peak = series.reduce((b, p) => (p.cpu_max > b.cpu_max ? p : b), series[0]);
    const low = series.reduce((b, p) => (p.mem_available < b.mem_available ? p : b), series[0]);
    const busy = [];
    if (series.filter((p) => p.cpu >= 85).length * T.step >= 20) busy.push({ level: 'busy', ts: peak.t, text: `The processor was maxed out, peaking at ${Math.round(peak.cpu_max)}% around ${at(peak.t)}${kinds.get(peak.t)?.top ? `; ${kinds.get(peak.t).top} was using the most` : ''}.` });
    else if (peak.cpu_max >= 60) lines.push({ level: 'note', ts: peak.t, text: `The processor got busy for a moment: ${Math.round(peak.cpu_max)}% at ${at(peak.t)}${kinds.get(peak.t)?.top ? `, mostly ${kinds.get(peak.t).top}` : ''}.` });
    else lines.push({ level: 'ok', text: `The processor was calm, never above ${Math.round(peak.cpu_max)}%.` });
    if (series.some((p) => p.pressure >= 2)) busy.push({ level: 'busy', ts: low.t, text: `Memory ran low, down to ${(low.mem_available / 1024 ** 3).toFixed(1)} GB free around ${at(low.t)}.` });
    else lines.push({ level: 'ok', text: 'Memory was fine.' });
    if (series.some((p) => p.thermal >= 1)) busy.push({ level: 'busy', ts: series.find((p) => p.thermal >= 1).t, text: 'The Mac was hot enough to slow itself down.' });
    const all = [...lines.filter((l) => l.level === 'problem'), ...busy, ...lines.filter((l) => l.level !== 'problem')];
    if (incidents.length) return { ...base, verdict: 'problem', headline: incidents[0].title, lines: all, focus: { incident: incidents[0].id } };
    if (busy.length) return { ...base, verdict: 'busy', headline: `The Mac was under strain ${label}`, lines: all, focus: { at: busy[0].ts } };
    all.push({ level: 'note', text: 'If something still felt slow, it may have been a website or the network, which Freezeframe can’t see.' });
    return { ...base, verdict: 'quiet', headline: `Nothing unusual happened ${label}`, lines: all, focus: null };
  }

  async function api(path, options) {
    const url = new URL(path, location.href);
    const q = Object.fromEntries(url.searchParams);
    const name = url.pathname.split('/api/')[1];

    if (name === 'overview') {
      return { ...shifted({ ...D.overview, incidents: [...added, ...D.overview.incidents] }), now: Date.now() / 1000 };
    }
    if (name === 'timeline') {
      const T = D.timeline;
      const lo = Number(q.start) - shift - T.step;
      const hi = Number(q.end) - shift + T.step;
      const events = [...T.events, ...added].filter((e) => (e.end_ts ?? e.ts) >= lo && e.ts <= hi);
      const appSeries = Object.fromEntries(Object.entries(T.app_series).map(([a, s]) => [a, s.filter(within(lo, hi))]));
      return shifted({ ...T, start: lo + T.step, end: hi - T.step, series: T.series.filter(within(lo, hi)), app_series: appSeries,
        kind_series: T.kind_series.filter(within(lo, hi)), events });
    }
    if (name === 'strip') return shifted(D.strip);
    if (name === 'hardware') return D.hardware;
    if (name === 'storage') return D.storage;
    if (name === 'what') {
      const w = parseWhen(q.q || '', Date.now() / 1000);
      if (!w) throw new Error("Freezeframe didn't understand that time.");
      return shifted(whatHappened(w.start - shift, w.end - shift, w.label));
    }
    if (name === 'now') {
      return shifted(D.now);
    }
    if (name === 'diagnose') {
      if (q.id) {
        const id = Number(q.id);
        const hit = D.incidents[id] || addedDiagnoses[id];
        if (!hit) throw new Error('no such incident');
        return shifted(hit);
      }
      return shifted({ event: null, diagnosis: nearestMoment(Number(q.at) - shift) });
    }
    if (name === 'mark') {
      const body = JSON.parse(options.body);
      const ts = Number(body.ts) - shift;
      const diagnosis = { ...nearestMoment(ts), start: ts, end: ts };
      const note = String(body.note || '').slice(0, 200);
      const event = {
        id: nextId++, ts, end_ts: ts, kind: 'mark', severity: 2, app: null, incident: true,
        title: `You reported ${note ? `“${note}”` : 'a problem'}: ${diagnosis.short}`,
        detail: note ? { note } : {}, short: diagnosis.short, confidence: diagnosis.confidence,
      };
      added.unshift(event);
      addedDiagnoses[event.id] = { event, diagnosis };
      return { event: shifted({ ...event, diagnosis }) };
    }
    throw new Error(`unknown endpoint ${path}`);
  }

  window.freezeframeApi = api;
})();
