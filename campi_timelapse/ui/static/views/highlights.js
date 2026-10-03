import {
  fill, api, badge, empty, fmt, h, offBanner, openPlayer, openSighting, post, prefs, qs, revealButton, selection, starButton,
  viewInTimelapse,
} from '../lib.js';

const TYPES = [
  ['new_catch', 'New catches'], ['rare', 'Rare'], ['busy_window', 'Busiest windows'], ['daily', 'Daily videos'], ['starred', 'Starred'],
];
const TYPE_LABEL = Object.fromEntries(TYPES);

// One card for any highlight item (also used by Today).
export function highlightCard(i, ids = null) {
  const tags = h('div', { class: 'flags' }, i.types.map((t) => badge(TYPE_LABEL[t] || t, `t-${t}`)));
  if (i.sighting) {
    const s = i.sighting;
    const card = h('article', { class: 'card hl sighting', dataset: { id: s.id } },
      h('div', { class: 'crop' }, s.crop_url ? h('img', { src: s.crop_url, loading: 'lazy', alt: s.label }) : null),
      h('div', { class: 'meta' },
        h('div', { class: 'label' }, s.label),
        h('div', { class: 'sub' }, h('span', {}, `${fmt.date(s.started_local)} ${fmt.hm(s.started_local)}`),
          h('span', { class: 'muted' }, `${s.label_count}× total`), badge(fmt.labelBy(s.label_by), `by-${s.label_by}`)),
        tags),
      starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { value: v })));
    card.addEventListener('click', () => openSighting(s.id, ids ? { ids } : null));
    return card;
  }
  if (i.type === 'busy_window') {
    const w = i.window;
    return h('article', { class: 'card hl video', onclick: () => viewInTimelapse(i.seek_ts, `Busy ${fmt.hm(w.start_local)}`) },
      h('div', { class: 'tile-icon' }, String(w.sightings)),
      h('div', { class: 'meta' },
        h('div', { class: 'label' }, `${fmt.hm(w.start_local)}–${fmt.hm(w.end_local)}`),
        h('div', { class: 'sub' }, h('span', {}, fmt.day(w.day)), h('span', { class: 'muted' }, `${w.sightings} sightings`),
          i.clip ? badge('clip', 'ok') : badge('daily only', 'muted')),
        tags));
  }
  if (i.daily) {
    const d = i.daily;
    return h('article', { class: 'card hl video', onclick: () => openPlayer({ url: d.url, title: `Day ${d.day}`, reveal: revealButton('daily', d.name) }) },
      h('div', { class: 'tile-icon' }, '▶'),
      h('div', { class: 'meta' }, h('div', { class: 'label' }, fmt.day(d.day)), h('div', { class: 'sub' }, h('span', { class: 'muted' }, fmt.size(d.size))), tags),
      starButton(d.starred, (v) => post(`/api/videos/daily/${d.name}/star`, { value: v })));
  }
  // starred 10-minute clip (playable while it exists, else via the daily video)
  const c = i.clip;
  const play = () => (c ? openPlayer({ url: c.url, title: c.name, reveal: revealButton('clips', c.name) }) : viewInTimelapse(i.seek_ts, i.title));
  return h('article', { class: 'card hl video', onclick: play },
    h('div', { class: 'tile-icon' }, '▶'),
    h('div', { class: 'meta' }, h('div', { class: 'label' }, i.title), h('div', { class: 'sub' }, c ? badge('clip', 'ok') : badge('clip expired', 'muted')), tags));
}

export async function mount(el) {
  let want = new Set(prefs.get('highlightTypes', TYPES.map(([t]) => t)));
  const chips = h('div', { class: 'chips' });
  const list = h('div', { class: 'hl-list' });
  const more = h('button', { class: 'ghost more', hidden: true }, 'Load more');
  const st = await api('/api/status').catch(() => null);
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Highlights'), chips), offBanner(st) || '', list, more);
  let cursor = null;
  let alive = true;
  let lastDay = null;
  let group = null;

  function drawChips() {
    fill(chips, ...TYPES.map(([t, label]) => h('button', {
      class: `chip ${want.has(t) ? 'on' : ''}`,
      onclick: () => {
        if (want.has(t) && want.size > 1) want.delete(t); else want.add(t);
        prefs.set('highlightTypes', [...want]);
        drawChips();
        load(true);
      },
    }, label)));
  }

  async function load(reset) {
    if (reset) { cursor = null; list.replaceChildren(); lastDay = null; }
    let page;
    try { page = await api(`/api/highlights${qs({ type: [...want].join(','), before: cursor, limit: 60 })}`); } catch (e) { list.append(empty('Could not load highlights', e.message)); return; }
    if (!alive) return;
    if (reset && !page.items.length) list.append(empty('No highlights yet', 'New catches, rare vehicles, busy windows, daily videos and anything you star show up here.'));
    for (const i of page.items) {
      const day = new Date(i.at);
      const key = fmt.localDate(new Date(day.getTime() - (i.type === 'daily' || i.type === 'busy_window' ? 1000 : 0)));
      if (key !== lastDay) {
        lastDay = key;
        group = h('div', { class: 'grid' });
        list.append(h('h3', { class: 'day-head' }, fmt.day(key)), group);
      }
      group.append(highlightCard(i));
    }
    cursor = page.next_cursor;
    more.hidden = !cursor;
    selection.set([...list.querySelectorAll('.card')], { open: (n) => n.click(), star: (n) => n.querySelector('.star')?.click() });
  }
  more.addEventListener('click', () => load(false));
  drawChips();
  await load(true);
  return { unmount() { alive = false; } };
}
