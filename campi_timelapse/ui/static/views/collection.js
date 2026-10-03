import {
  api, badge, counter, empty, fill, fmt, h, offBanner, prefs, qs, ring, selection, sightingsOff, stagger, tabs, tierSegments, viewHead,
} from '../lib.js';

const SHOWS = [['all', 'All'], ['caught', 'Caught'], ['uncaught', 'Not yet'], ['discovered', 'Discovered']];
const SORTS = [['list', 'List order'], ['count', 'Most seen'], ['recent', 'Recently seen'], ['rarity', 'Rarest first']];
const TIER_ORDER = { legendary: 0, rare: 1, uncommon: 2, common: 3, uncaught: 4 };
const TIERS = ['legendary', 'rare', 'uncommon', 'common'];

export async function mount(el) {
  const [st, c] = await Promise.all([api('/api/status').catch(() => null), api('/api/collection').catch((e) => ({ error: e }))]);
  const head = viewHead();
  el.append(head);
  if (st && !st.sightings_enabled && !st.sightings_db) {
    el.append(sightingsOff());
    return { unmount() {} };
  }
  if (c.error) { el.append(empty('Could not load the collection', c.error.message)); return { unmount() {} }; }
  const banner = offBanner(st);
  if (banner) el.append(banner);

  let show = prefs.get('collectionShow', 'all');
  let sort = prefs.get('collectionSort', 'list');
  const frac = c.total ? c.caught / c.total : 0;
  const tierCount = Object.fromEntries(TIERS.map((t) => [t, c.items.filter((i) => i.tier === t).length]));
  el.append(h('section', { class: 'panel coll-head' },
    ring(tierSegments(c.items), c.total, [h('b', {}, counter('coll-caught', c.caught)), h('span', { class: 'muted' }, `of ${c.total} caught`)]),
    h('div', { class: 'coll-stats' },
      h('h2', {}, `${Math.round(frac * 100)}% of the list caught`),
      h('div', { class: 'progress' }, h('div', { class: 'bar', style: { width: `${frac * 100}%` } })),
      h('div', { class: 'tiles' },
        TIERS.map((t) => h('div', { class: `tile tier-${t}` }, h('span', { class: 'cap' }, t), h('b', {}, counter(`tier-${t}`, tierCount[t])))),
        h('div', { class: 'tile' }, h('span', { class: 'cap' }, 'discovered'), h('b', { class: 'num' }, String(c.discovered))),
        h('div', { class: 'tile' }, h('span', { class: 'cap' }, 'not yet'), h('b', { class: 'num' }, String(c.total - c.caught)))))));
  const grid = h('div', { class: 'tiles-grid' });
  el.append(grid);

  function draw() {
    fill(head,
      tabs(SHOWS, (k) => show === k, (k) => { show = k; prefs.set('collectionShow', k); draw(); }),
      h('div', { class: 'grow' }),
      h('select', { class: 'pill-select', onchange: (e) => { sort = e.target.value; prefs.set('collectionSort', sort); draw(); } },
        SORTS.map(([k, l]) => h('option', { value: k, selected: sort === k }, l))));
    let items = c.items.filter((i) => show === 'all' || (show === 'caught' && i.count) || (show === 'uncaught' && !i.count) || (show === 'discovered' && i.discovered));
    if (sort === 'count') items = [...items].sort((a, b) => b.count - a.count);
    if (sort === 'recent') items = [...items].sort((a, b) => (b.last_seen || '').localeCompare(a.last_seen || ''));
    if (sort === 'rarity') items = [...items].sort((a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || a.count - b.count);
    fill(grid, ...stagger(items.map(tile)));
    if (!items.length) grid.append(empty('Nothing here yet'));
    selection.set([...grid.querySelectorAll('.ctile')], { open: (n) => n.click() });
  }

  function tile(i) {
    const caught = i.count > 0;
    return h('article', {
      class: `ctile tier-${i.tier} ${caught ? '' : 'uncaught'}`,
      title: caught ? `Show the ${i.count} sighting(s) of ${i.label}` : 'Not seen yet',
      onclick: () => { if (caught) location.hash = `#/sightings${qs({ label: i.label })}`; },
    },
    h('div', { class: 'ct-top' }, h('span', { class: 'cap' }, i.generic ? 'type' : (i.make || '')), h('div', { class: 'grow' }),
      i.discovered ? badge('discovered', 't-discovered') : null, badge(i.tier, `tier ${i.tier}`)),
    h('div', { class: 'ct-name' }, i.generic ? i.label : (i.model || i.label)),
    caught
      ? h('div', { class: 'ct-stats' }, h('b', { class: 'num' }, `${i.count}`, h('small', {}, '×')),
        h('span', { class: 'muted num' }, `first ${fmt.date(i.first_seen_local).slice(5)} · last ${fmt.hm(i.last_seen_local)}`))
      : h('div', { class: 'ct-stats muted' }, 'not seen yet'));
  }

  draw();
  return { unmount() {} };
}
