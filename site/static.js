// Stands in for the local Freezeframe server on the public demo website.
// It answers the dashboard's API calls from exported demo data, shifted so the
// recording ends a minute ago, and supports "Something went wrong" in memory.
(function () {
  const D = window.FREEZEFRAME_DEMO;
  const shift = Date.now() / 1000 - 60 - D.overview.last;
  const TIME_KEYS = new Set(['ts', 'end_ts', 't', 'first', 'last', 'start', 'end', 'started']);
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
      return shifted({ ...T, start: lo + T.step, end: hi - T.step, series: T.series.filter(within(lo, hi)), app_series: appSeries, events });
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
