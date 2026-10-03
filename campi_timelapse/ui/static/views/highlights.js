import {
  api, badge, empty, fmt, h, icon, offBanner, openPlayer, openSighting, post, prefs, qs, revealButton, selection,
  stagger, starButton, tabs, viewHead, viewInTimelapse,
} from '../lib.js';

const TYPES = [
  ['new_catch', 'New catches'], ['rare', 'Rare'], ['busy_window', 'Busiest windows'], ['daily', 'Daily videos'], ['starred', 'Starred'],
];
const TYPE_TAG = { new_catch: 'New', rare: 'Rare', busy_window: 'Busy', daily: 'Daily', starred: 'Starred' };

// One card for any highlight item (also used by Today).
export function highlightCard(i, ids = null) {
  const tags = h('div', { class: 'crop-flags' }, i.types.map((t) => badge(TYPE_TAG[t] || t, `t-${t}`)));
  if (i.sighting) {
    const s = i.sighting;
    const card = h('article', { class: `card hl sighting ${i.types.includes('new_catch') ? 'is-new' : ''}`, dataset: { id: s.id } },
      h('div', { class: 'crop' }, s.crop_url ? h('img', { src: s.crop_url, loading: 'lazy', alt: s.label }) : null, tags,
        starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { value: v }))),
      h('div', { class: 'meta' },
        h('div', { class: 'label' }, s.label),
        h('div', { class: 'sub' }, h('span', { class: 'num' }, fmt.hm(s.started_local)), h('span', {}, fmt.day(fmt.date(s.started_local))),
          h('span', { class: 'grow' }), h('span', { class: 'num muted' }, `${s.label_count}×`), badge(fmt.labelBy(s.label_by), `by-${s.label_by}`))));
    card.addEventListener('click', () => openSighting(s.id, ids ? { ids } : null));
    return card;
  }
  if (i.type === 'busy_window') {
    const w = i.window;
    return h('article', { class: 'card hl tile-card', onclick: () => viewInTimelapse(i.seek_ts, `Busy ${fmt.hm(w.start_local)}`) },
      h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, fmt.day(w.day)), h('span', { class: 'tile-ic' }, icon('bars', 18))),
      h('b', { class: 'tc-big num' }, String(w.sightings), h('small', {}, 'sightings')),
      h('div', { class: 'tc-sub' }, h('span', { class: 'num' }, `${fmt.hm(w.start_local)}–${fmt.hm(w.end_local)}`), h('span', { class: 'grow' }),
        i.clip ? badge('clip', 'amber') : badge('daily only', 'muted')),
      tags);
  }
  if (i.daily) {
    const d = i.daily;
    return h('article', { class: 'card hl tile-card', onclick: () => openPlayer({ url: d.url, title: `Day ${fmt.day(d.day)}`, reveal: revealButton('daily', d.name) }) },
      h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, 'Daily video'), h('span', { class: 'tile-ic' }, icon('film', 18))),
      h('b', { class: 'tc-big' }, fmt.weekday(d.day), h('small', {}, d.day.slice(5))),
      h('div', { class: 'tc-sub' }, h('span', { class: 'num muted' }, fmt.size(d.size)), h('span', { class: 'grow' }),
        starButton(d.starred, (v) => post(`/api/videos/daily/${d.name}/star`, { value: v }))),
      tags);
  }
  // starred 10-minute clip (playable while it exists, else via the daily video)
  const c = i.clip;
  const play = () => (c ? openPlayer({ url: c.url, title: c.name, reveal: revealButton('clips', c.name) }) : viewInTimelapse(i.seek_ts, i.title));
  return h('article', { class: 'card hl tile-card', onclick: play },
    h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, 'Starred clip'), h('span', { class: 'tile-ic' }, icon('star', 18))),
    h('b', { class: 'tc-big num' }, (i.title.match(/\d\d:\d\d$/) || [''])[0]),
    h('div', { class: 'tc-sub' }, h('span', { class: 'muted' }, i.title.replace(/^Starred clip /, '').slice(0, 10)), h('span', { class: 'grow' }),
      c ? badge('clip', 'amber') : badge('clip expired', 'muted')),
    tags);
}

export async function mount(el) {
  const want = new Set(prefs.get('highlightTypes', TYPES.map(([t]) => t)));
  const head = viewHead();
  const list = h('div', { class: 'hl-list' });
  const more = h('button', { class: 'pill-btn more', hidden: true }, 'Load more');
  const st = await api('/api/status').catch(() => null);
  el.append(head, offBanner(st) || '', list, more);
  let cursor = null;
  let alive = true;
  let lastDay = null;
  let group = null;

  function drawTabs() {
    head.replaceChildren(tabs(TYPES, (t) => want.has(t), (t) => {
      if (want.has(t) && want.size > 1) want.delete(t); else want.add(t);
      prefs.set('highlightTypes', [...want]);
      drawTabs();
      load(true);
    }));
  }

  async function load(reset) {
    if (reset) { cursor = null; list.replaceChildren(); lastDay = null; }
    let page;
    try { page = await api(`/api/highlights${qs({ type: [...want].join(','), before: cursor, limit: 60 })}`); } catch (e) { list.append(empty('Could not load highlights', e.message)); return; }
    if (!alive) return;
    if (reset && !page.items.length) list.append(empty('No highlights yet', 'New catches, rare vehicles, busy windows, daily videos and anything you star show up here.'));
    const added = [];
    for (const i of page.items) {
      const at = new Date(i.at);
      const key = fmt.localDate(new Date(at.getTime() - (i.type === 'daily' || i.type === 'busy_window' ? 1000 : 0)));
      if (key !== lastDay) {
        lastDay = key;
        group = h('div', { class: 'grid' });
        list.append(h('h3', { class: 'day-head' }, fmt.day(key)), group);
      }
      const card = highlightCard(i);
      added.push(card);
      group.append(card);
    }
    stagger(added);
    cursor = page.next_cursor;
    more.hidden = !cursor;
    selection.set([...list.querySelectorAll('.card')], { open: (n) => n.click(), star: (n) => n.querySelector('.star')?.click() });
  }
  more.addEventListener('click', () => load(false));
  drawTabs();
  await load(true);
  return { unmount() { alive = false; } };
}
