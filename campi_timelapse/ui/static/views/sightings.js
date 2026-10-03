import {
  api, empty, h, offBanner, openSighting, prefs, qs, selection, sightingCard, sightingsOff,
} from '../lib.js';

const CLASSES = ['car', 'truck', 'bus', 'motorcycle'];
const SOURCES = [['siglip', 'SigLIP'], ['cloud', 'Gemini'], ['user', 'You']];

export async function mount(el, params) {
  const st = await api('/api/status').catch(() => null);
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Sightings')));
  if (st && !st.sightings_enabled && !st.sightings_db) {
    el.append(sightingsOff(st));
    return { unmount() {} };
  }
  const banner = offBanner(st);
  if (banner) el.append(banner);

  // filters: URL params (from Collection) win over the remembered ones
  const f = { from: '', to: '', make: '', class: '', source: '', label: '', hide_unsure: false, hide_stationary: false, starred: false, include_hidden: false, ...prefs.get('sightingFilters', {}) };
  if (Object.keys(params).length) Object.assign(f, { make: '', label: '', class: '', source: '' }, params);
  let makes = [];
  try {
    const c = await api('/api/collection');
    makes = [...new Set(c.items.filter((i) => i.count && i.make).map((i) => i.make))].sort();
  } catch { /* filters still work without the list */ }

  const input = (key, type = 'date') => h('input', { type, value: f[key], onchange: (e) => set(key, e.target.value) });
  const select = (key, opts, all) => h('select', { onchange: (e) => set(key, e.target.value) },
    h('option', { value: '' }, all), opts.map(([v, l]) => h('option', { value: v, selected: f[key] === v }, l)));
  const check = (key, label) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!f[key], onchange: (e) => set(key, e.target.checked) }), label);
  const bar = h('div', { class: 'filters' },
    h('label', {}, 'From ', input('from')), h('label', {}, 'To ', input('to')),
    select('make', makes.map((m) => [m, m]), 'All makes'),
    select('class', CLASSES.map((c) => [c, c]), 'All classes'),
    select('source', SOURCES, 'Any source'),
    check('hide_unsure', 'Hide unsure'), check('hide_stationary', 'Hide parked'), check('starred', 'Starred'), check('include_hidden', 'Show hidden'),
    f.label ? h('span', { class: 'chip on', title: 'clear', onclick: () => set('label', '') }, `${f.label} ✕`) : null);
  const grid = h('div', { class: 'grid' });
  const sentinel = h('div', { class: 'sentinel' });
  const count = h('span', { class: 'muted' });
  el.querySelector('.view-head').append(count);
  el.append(bar, grid, sentinel);

  let cursor = null;
  let loading = false;
  let done = false;
  let alive = true;
  let gen = 0;
  const ids = [];

  function set(key, value) {
    f[key] = value;
    const { label, ...keep } = f; // a label filter comes from Collection; don't remember it
    prefs.set('sightingFilters', keep);
    if (key === 'label') { location.hash = '#/sightings'; return; }
    reset();
  }

  function reset() {
    gen += 1;
    cursor = null; done = false; ids.length = 0;
    grid.replaceChildren();
    load();
  }

  const listCtx = {
    ids,
    onChange: (r) => {
      const card = grid.querySelector(`[data-id="${r.id}"]`);
      if (card) card.replaceWith(sightingCard(r, open));
      refreshSelection();
    },
    onStep: (id) => { const n = grid.querySelector(`[data-id="${id}"]`); if (n) selection.select(n); },
  };
  function open(s, card) { if (card) selection.select(card); openSighting(s.id, listCtx); }
  function refreshSelection() {
    selection.set([...grid.querySelectorAll('.card')], { open: (n) => n.click(), star: (n) => n.querySelector('.star')?.click() });
  }

  async function load() {
    if (loading || done) return;
    loading = true;
    const my = gen;
    try {
      const page = await api(`/api/sightings${qs({ ...f, cursor, limit: 60 })}`);
      if (!alive || my !== gen) return;
      for (const s of page.items) { ids.push(s.id); grid.append(sightingCard(s, open)); }
      cursor = page.next_cursor;
      done = !cursor;
      if (!ids.length) grid.append(empty('No sightings match', 'Try clearing some filters.'));
      count.textContent = `${ids.length}${done ? '' : '+'} shown`;
      refreshSelection();
    } catch (e) {
      grid.append(empty('Could not load sightings', e.message));
      done = true;
    } finally {
      loading = false;
    }
    if (!done && sentinel.getBoundingClientRect().top < window.innerHeight + 600) load();
  }

  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) load(); }, { root: null, rootMargin: '600px' });
  io.observe(sentinel);
  await load();
  return { unmount() { alive = false; io.disconnect(); } };
}
