import {
  api, badge, clipFile, dailyFile, empty, epoch, fmt, h, icon, offBanner, openPlayer, openSighting, post, prefs, qs,
  revealButton, selection, stagger, starButton, tabs, viewHead, viewInTimelapse,
} from '../lib.js';

const TYPES = [
  ['new_catch', 'New catches'], ['rare', 'Rare'], ['busiest', 'Busiest windows'], ['daily', 'Daily videos'], ['starred', 'Starred'],
];
const TYPE_TAG = { new_catch: 'New', rare: 'Rare', busiest: 'Busy', daily: 'Daily', starred: 'Starred' };

const playClip = (c) => openPlayer({ url: c.media.video, title: `Clip ${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`, reveal: revealButton('clips', clipFile(c.id)) });

// One card for any highlight item (also used by Today).
export function highlightCard(i, ids = null) {
  const tags = h('div', { class: 'crop-flags' }, i.types.map((t) => badge(TYPE_TAG[t] || t, `t-${t}`)));
  if (i.sighting) {
    const s = i.sighting;
    const card = h('article', { class: `card hl sighting ${i.types.includes('new_catch') ? 'is-new' : ''}`, dataset: { id: s.id } },
      h('div', { class: 'crop' }, s.media.crop ? h('img', { src: s.media.crop, loading: 'lazy', alt: s.label }) : null, tags,
        starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { starred: v }))),
      h('div', { class: 'meta' },
        h('div', { class: 'label' }, s.label),
        h('div', { class: 'sub' }, h('span', { class: 'num' }, fmt.hm(s.started_at)), h('span', {}, fmt.day(s.day)),
          h('span', { class: 'grow' }), badge(fmt.labelBy(s.decided_by), `by-${s.decided_by}`))));
    card.addEventListener('click', () => openSighting(s.id, ids ? { ids } : null));
    return card;
  }
  if (i.window) { // a busiest window, or a starred clip (its file may be gone: then the daily video)
    const w = i.window;
    const c = i.clip;
    const busy = i.types.includes('busiest');
    const play = () => (c ? playClip(c) : viewInTimelapse(epoch(w.start) + 1, `${busy ? 'Busy' : 'Starred'} ${fmt.hm(w.start)}`));
    return h('article', { class: 'card hl tile-card', onclick: play },
      h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, busy ? fmt.day(i.day) : 'Starred clip'), h('span', { class: 'tile-ic' }, icon(busy ? 'bars' : 'star', 18))),
      busy ? h('b', { class: 'tc-big num' }, String(w.sightings_count), h('small', {}, 'sightings')) : h('b', { class: 'tc-big num' }, fmt.hm(w.start)),
      h('div', { class: 'tc-sub' }, h('span', { class: busy ? 'num' : 'muted' }, busy ? `${fmt.hm(w.start)}–${fmt.hm(w.end)}` : i.day), h('span', { class: 'grow' }),
        c ? badge('clip', 'amber') : badge(busy ? 'daily only' : 'clip expired', 'muted')),
      tags);
  }
  if (i.daily) {
    const d = i.daily;
    return h('article', { class: 'card hl tile-card', onclick: () => openPlayer({ url: d.media.video, title: `Day ${fmt.day(d.day)}`, reveal: revealButton('daily', dailyFile(d.day)) }) },
      h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, 'Daily video'), h('span', { class: 'tile-ic' }, icon('film', 18))),
      h('b', { class: 'tc-big' }, fmt.weekday(d.day), h('small', {}, d.day.slice(5))),
      h('div', { class: 'tc-sub' }, h('span', { class: 'num muted' }, fmt.size(d.size_bytes)), h('span', { class: 'grow' }),
        starButton(d.starred, (v) => post(`/api/daily/${d.day}/star`, { starred: v }))),
      tags);
  }
  // starred 10-minute clip that still exists
  const c = i.clip;
  return h('article', { class: 'card hl tile-card', onclick: () => playClip(c) },
    h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, 'Starred clip'), h('span', { class: 'tile-ic' }, icon('star', 18))),
    h('b', { class: 'tc-big num' }, fmt.hm(c.window_start)),
    h('div', { class: 'tc-sub' }, h('span', { class: 'muted' }, c.day), h('span', { class: 'grow' }), badge('clip', 'amber')),
    tags);
}

export async function mount(el) {
  const known = TYPES.map(([t]) => t);
  const saved = prefs.get('highlightTypes', known).map((t) => (t === 'busy_window' ? 'busiest' : t)).filter((t) => known.includes(t));
  const want = new Set(saved.length ? saved : known);
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
    try { page = await api(`/api/highlights${qs({ type: [...want].join(','), cursor, limit: 60 })}`); } catch (e) { list.append(empty('Could not load highlights', e.message)); return; }
    if (!alive) return;
    if (reset && !page.items.length) list.append(empty('No highlights yet', 'New catches, rare vehicles, busy windows, daily videos and anything you star show up here.'));
    const added = [];
    for (const i of page.items) {
      const key = i.day;
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
