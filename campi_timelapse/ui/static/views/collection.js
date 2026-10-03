import { fill, api, badge, empty, fmt, h, offBanner, prefs, qs, selection, sightingsOff } from '../lib.js';

const SHOWS = [['all', 'All'], ['caught', 'Caught'], ['uncaught', 'Not yet'], ['discovered', 'Discovered']];
const SORTS = [['list', 'List order'], ['count', 'Most seen'], ['recent', 'Recently seen'], ['rarity', 'Rarest first']];
const TIER_ORDER = { legendary: 0, rare: 1, uncommon: 2, common: 3, uncaught: 4 };

export async function mount(el) {
  const [st, c] = await Promise.all([api('/api/status').catch(() => null), api('/api/collection').catch((e) => ({ error: e }))]);
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Collection')));
  if (st && !st.sightings_enabled && !st.sightings_db) {
    el.append(sightingsOff(st));
    return { unmount() {} };
  }
  if (c.error) { el.append(empty('Could not load the collection', c.error.message)); return { unmount() {} }; }
  const banner = offBanner(st);
  if (banner) el.append(banner);

  let show = prefs.get('collectionShow', 'all');
  let sort = prefs.get('collectionSort', 'list');
  const pct = c.total ? Math.round((100 * c.caught) / c.total) : 0;
  const head = h('div', { class: 'progress-wrap' },
    h('div', { class: 'progress-text' }, h('b', {}, `${c.caught} of ${c.total}`), ' caught', c.discovered ? ` · ${c.discovered} discovered by Gemini` : ''),
    h('div', { class: 'progress' }, h('div', { class: 'bar', style: { width: `${pct}%` } })));
  const controls = h('div', { class: 'filters' });
  const grid = h('div', { class: 'tiles' });
  el.append(head, controls, grid);

  function draw() {
    fill(controls,
      ...SHOWS.map(([k, l]) => h('button', { class: `chip ${show === k ? 'on' : ''}`, onclick: () => { show = k; prefs.set('collectionShow', k); draw(); } }, l)),
      h('div', { class: 'grow' }),
      h('select', { onchange: (e) => { sort = e.target.value; prefs.set('collectionSort', sort); draw(); } },
        SORTS.map(([k, l]) => h('option', { value: k, selected: sort === k }, l))));
    let items = c.items.filter((i) => show === 'all' || (show === 'caught' && i.count) || (show === 'uncaught' && !i.count) || (show === 'discovered' && i.discovered));
    if (sort === 'count') items = [...items].sort((a, b) => b.count - a.count);
    if (sort === 'recent') items = [...items].sort((a, b) => (b.last_seen || '').localeCompare(a.last_seen || ''));
    if (sort === 'rarity') items = [...items].sort((a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || a.count - b.count);
    fill(grid, ...items.map(tile));
    if (!items.length) grid.append(empty('Nothing here yet'));
    selection.set([...grid.querySelectorAll('.tile')], { open: (n) => n.click() });
  }

  function tile(i) {
    const caught = i.count > 0;
    return h('article', {
      class: `tile tier-${i.tier} ${caught ? '' : 'uncaught'}`,
      title: caught ? `Show the ${i.count} sighting(s) of ${i.label}` : 'Not seen yet',
      onclick: () => { if (caught) location.hash = `#/sightings${qs({ label: i.label })}`; },
    },
    h('div', { class: 'tile-top' }, h('span', { class: 'make' }, i.generic ? 'type' : (i.make || '')), h('div', { class: 'grow' }),
      i.discovered ? badge('discovered', 't-discovered') : null, badge(i.tier, `tier ${i.tier}`)),
    h('div', { class: 'tile-name' }, i.generic ? i.label : (i.model || i.label)),
    caught
      ? h('div', { class: 'tile-stats' }, h('b', {}, `${i.count}×`), h('span', { class: 'muted' }, `first ${fmt.date(i.first_seen_local)}`),
        h('span', { class: 'muted' }, `last ${fmt.date(i.last_seen_local)} ${fmt.hm(i.last_seen_local)}`))
      : h('div', { class: 'tile-stats muted' }, 'not seen yet'));
  }

  draw();
  return { unmount() {} };
}
