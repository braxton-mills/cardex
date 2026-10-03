import {
  api, empty, h, icon, offBanner, openSighting, prefs, qs, selection, sightingCard, sightingsOff, stagger, viewHead,
} from '../lib.js';

const CLASSES = ['car', 'truck', 'bus', 'motorcycle'];
const SOURCES = [['siglip', 'SigLIP'], ['cloud', 'Gemini'], ['user', 'You']];

export async function mount(el, params) {
  const st = await api('/api/status').catch(() => null);
  const head = viewHead();
  el.append(head);
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

  const input = (key) => h('label', { class: 'pill-field' },
    h('input', { type: 'date', value: f[key], title: key === 'from' ? 'From' : 'To', onchange: (e) => set(key, e.target.value) }));
  const select = (key, opts, all) => h('select', { class: 'pill-select', onchange: (e) => set(key, e.target.value) },
    h('option', { value: '' }, all), opts.map(([v, l]) => h('option', { value: v, selected: f[key] === v }, l)));
  const toggle = (key, label) => h('button', {
    class: `tab ${f[key] ? 'on' : ''}`, 'aria-pressed': String(!!f[key]),
    onclick: (e) => { const next = !f[key]; e.currentTarget.classList.toggle('on', next); e.currentTarget.setAttribute('aria-pressed', String(next)); set(key, next); },
  }, label);
  const bar = h('div', { class: 'filters' },
    input('from'), h('span', { class: 'muted' }, '–'), input('to'),
    select('make', makes.map((m) => [m, m]), 'All makes'),
    select('class', CLASSES.map((c) => [c, c]), 'All classes'),
    select('source', SOURCES, 'Any source'),
    h('div', { class: 'tabs' }, toggle('hide_unsure', 'Hide unsure'), toggle('hide_stationary', 'Hide parked'), toggle('starred', 'Starred'), toggle('include_hidden', 'Show hidden')),
    f.label ? h('button', { class: 'tab on', title: 'Clear the label filter', onclick: () => set('label', '') }, icon('tag', 14), f.label, icon('x', 14)) : null);
  const grid = h('div', { class: 'grid' });
  const sentinel = h('div', { class: 'sentinel' });
  const count = h('span', { class: 'count-pill num' });
  head.append(bar, h('div', { class: 'grow' }), count);
  el.append(grid, sentinel);

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
      const cards = page.items.map((s) => { ids.push(s.id); return sightingCard(s, open); });
      grid.append(...cards);
      stagger(cards);
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
