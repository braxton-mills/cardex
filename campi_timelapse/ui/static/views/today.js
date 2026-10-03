import {
  fill, api, empty, fmt, h, offBanner, openSighting, post, revealButton, selection, sightingCard, sightingsOff, starButton, statusStrip,
} from '../lib.js';
import { highlightCard } from './highlights.js';

const REFRESH_MS = 30000;

export async function mount(el) {
  const strip = h('div', { class: 'strip-wrap' });
  const player = h('section', { class: 'panel newest' });
  const counts = h('section', { class: 'panel counts' });
  const latest = h('section', { class: 'panel' });
  const hl = h('section', { class: 'panel' });
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Today'), h('span', { class: 'muted' }, fmt.day(fmt.localDate()))),
    strip, h('div', { class: 'today-top' }, player, counts), latest, hl);

  let clipName = null;
  let video = null;
  let timer = null;
  let alive = true;

  async function refresh() {
    let st;
    try { st = await api('/api/status'); } catch (e) { fill(strip, empty('Status unavailable', e.message)); return; }
    if (!alive) return;
    fill(strip, statusStrip(st));
    await Promise.all([newest(), sightingsPart(st)]);
    if (alive) selection.set([...el.querySelectorAll('.card.sighting, .card.hl')], {
      open: (n) => n.click(),
      star: (n) => n.querySelector('.star')?.click(),
    });
  }

  let shown = null; // clip info currently in the player
  let touched = false; // you paused, seeked or unmuted it: don't swap it under you

  function load(c) {
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    shown = c;
    clipName = c.name;
    touched = false;
    video = h('video', { src: c.url, controls: true, muted: true, autoplay: true, loop: true, playsinline: true, preload: 'auto' });
    const touch = () => { touched = true; };
    video.addEventListener('seeking', touch);
    video.addEventListener('volumechange', touch);
    video.addEventListener('pause', () => { if (!video.ended) touch(); });
    fill(player, h('header', { class: 'row' }), video);
    headerFor(c, null);
  }

  async function newest() {
    let c = null;
    try { c = await api('/api/clips/newest'); } catch { /* none yet */ }
    if (!alive) return;
    if (!c) { fill(player, h('h2', {}, 'Newest clip'), empty('No clips yet')); clipName = null; return; }
    if (c.name === clipName) { headerFor(c, null); return; }
    if (video && touched) { headerFor(shown, c); return; } // offer it instead
    load(c);
  }

  function headerFor(c, newer) {
    const hdr = player.querySelector('header');
    if (!hdr) return;
    fill(hdr, h('h2', {}, `Newest clip · ${fmt.hm(c.start_local)}–${fmt.hm(c.end_local)}`),
      c.sightings != null ? h('span', { class: 'muted' }, `${c.sightings} sightings`) : null,
      h('div', { class: 'grow' }),
      newer ? h('button', { class: 'primary small', onclick: () => load(newer) }, `Newer clip: ${fmt.hm(newer.start_local)}`) : null,
      starButton(c.starred, (v) => post(`/api/videos/clips/${c.name}/star`, { value: v })),
      revealButton('clips', c.name));
  }

  async function sightingsPart(st) {
    if (!st.sightings_enabled && !st.sightings_db) {
      fill(counts, sightingsOff(st));
      latest.hidden = true;
      hl.hidden = true;
      return;
    }
    latest.hidden = false;
    hl.hidden = false;
    const [t, page, hls] = await Promise.all([
      api('/api/today'), api('/api/sightings?limit=12'), api('/api/highlights?limit=200&type=new_catch,rare,busy_window,starred'),
    ]);
    if (!alive) return;
    const by = Object.entries(t.by_class).sort((a, b) => b[1] - a[1]);
    fill(counts,
      h('h2', {}, 'Today'),
      offBanner(st),
      h('div', { class: 'big-numbers' },
        num(t.total, 'sightings'), num(t.labels, 'different vehicles'), num(t.new_catches, 'new catches'),
        num(t.by_label_by.cloud || 0, 'decided by Gemini')),
      h('div', { class: 'by-class' }, by.map(([k, v]) => h('span', { class: 'chip static' }, `${k} ${v}`))));
    const ids = page.items.map((s) => s.id);
    const open = (s) => openSighting(s.id, { ids, onChange: () => {} });
    fill(latest, h('header', { class: 'row' }, h('h2', {}, 'Latest sightings'), h('div', { class: 'grow' }), h('a', { href: '#/sightings' }, 'All sightings →')),
      page.items.length ? h('div', { class: 'grid' }, page.items.map((s) => sightingCard(s, open))) : empty('No sightings yet today'));
    const todayStart = new Date(`${fmt.localDate()}T00:00:00`).getTime();
    const todays = hls.items.filter((i) => new Date(i.at).getTime() >= todayStart);
    fill(hl, h('header', { class: 'row' }, h('h2', {}, "Today's highlights"), h('div', { class: 'grow' }), h('a', { href: '#/highlights' }, 'All highlights →')),
      todays.length ? h('div', { class: 'grid' }, todays.slice(0, 24).map((i) => highlightCard(i))) : empty('Nothing special yet today'));
  }

  const num = (n, label) => h('div', { class: 'num' }, h('b', {}, String(n)), h('span', {}, label));

  await refresh();
  timer = setInterval(refresh, REFRESH_MS);
  return {
    unmount() { alive = false; clearInterval(timer); if (video) { video.pause(); video.removeAttribute('src'); video.load(); } },
    video: () => video,
  };
}

