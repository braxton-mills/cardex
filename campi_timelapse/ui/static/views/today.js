// Today: dashboard. Hero = the newest 10-minute clip (new sightings "drive by" across it); under it,
// collection ring + sightings chart; right column = camera status, latest sighting (player-style), mix by class.
import {
  api, badge, cardHead, clipFile, confetti, counter, empty, epoch, fill, fmt, h, icon, iconButton, listStats,
  offBanner, openPlayer, openSighting, post, reduceMotion, releaseVideo, revealButton, ring, seekTs, selection,
  sightingCard, sightingsOff, stagger, starButton, tierSegments, toast, viewInTimelapse,
} from '../lib.js';
import { highlightCard } from './highlights.js';

const REFRESH_MS = 30000;
const MAX_DRIVEBYS = 3;

export async function mount(el) {
  const hero = h('section', { class: 'hero' });
  const status = h('section', { class: 'panel status-card' });
  const latestCard = h('section', { class: 'panel now-card' });
  const mix = h('section', { class: 'panel mix-card' });
  const collCard = h('section', { class: 'panel coll-card' });
  const chart = h('section', { class: 'panel chart-card' });
  const latest = h('section', { class: 'panel' });
  const hl = h('section', { class: 'panel' });
  const banner = h('div');
  el.append(banner,
    h('div', { class: 'dash' }, hero, h('div', { class: 'hero-cards' }, collCard, chart), h('div', { class: 'side' }, status, latestCard, mix)),
    latest, hl);

  let alive = true;
  let first = true;
  let video = null;
  let shown = null; // clip in the hero
  let touched = false; // you paused or seeked it: don't swap it under you
  let chartMode = 'today';
  let picked = null; // chart bar you clicked
  let chartAnim = true; // grow the bars on the next render
  let nowIdx = 0; // latest-sighting card position
  let lastSightings = [];
  let lastCaught = null;
  const seenIds = new Set(); // sightings already on screen (new ones pop in and drive by)
  const seenCatches = new Set(); // today's new catches already celebrated

  // ------------------------------------------------------------ hero: newest clip + drive-bys

  function loadClip(c) {
    releaseVideo(video);
    shown = c;
    touched = false;
    video = h('video', { src: c.media.video, muted: true, autoplay: true, loop: true, playsinline: true, preload: 'auto' });
    video.addEventListener('seeking', () => { touched = true; });
    video.addEventListener('pause', () => { if (!video.ended) touched = true; });
    video.addEventListener('click', () => (video.paused ? video.play().catch(() => {}) : video.pause()));
    hero.querySelectorAll('video, .hero-shade, .hero-top, .empty').forEach((n) => n.remove());
    hero.prepend(video, h('div', { class: 'hero-shade' }), h('div', { class: 'hero-top' }));
    heroOverlay(c, null);
  }

  function heroOverlay(c, newer) {
    const top = hero.querySelector('.hero-top');
    if (!top) return;
    fill(top,
      h('div', { class: 'hero-title' },
        h('span', { class: 'live-dot' }), h('span', {}, 'Newest clip'),
        h('b', { class: 'num' }, `${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`),
        c.sightings_count != null ? h('span', { class: 'muted' }, `· ${c.sightings_count} sightings`) : null),
      h('div', { class: 'grow' }),
      newer ? h('button', { class: 'pill-btn amber', onclick: () => loadClip(newer) }, icon('arrowRight', 16), `Newer: ${fmt.hm(newer.window_start)}`) : null,
      starButton(c.starred, (v) => post(`/api/clips/${c.id}/star`, { starred: v })),
      revealButton('clips', clipFile(c.id)),
      iconButton('maximize', 'Open in player', () => openPlayer({ url: c.media.video, title: `Clip ${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`, reveal: revealButton('clips', clipFile(c.id)) })));
  }

  async function newest() {
    let c = null;
    try { c = await api('/api/clips/newest'); } catch { /* none yet */ }
    if (!alive) return;
    if (!c) { fill(hero, empty('No clips yet', 'The first 10-minute clip appears shortly after capture starts.')); shown = null; return; }
    if (shown && c.id === shown.id) { heroOverlay(c, null); return; }
    if (video && touched) { heroOverlay(shown, c); return; } // offer it instead
    loadClip(c);
  }

  // A new sighting crosses the hero the way the car went (LR / RL); click it to open the sighting.
  function driveBy(s, delay) {
    if (reduceMotion()) return;
    const rl = s.direction === 'RL';
    const pill = h('div', { class: `driveby ${rl ? 'rl' : 'lr'}`, style: { animationDelay: `${delay}ms` }, title: 'Open this sighting',
      onclick: () => openSighting(s.id) },
    s.media.crop ? h('img', { src: s.media.crop, alt: '' }) : h('span', { class: 'head-ic' }, icon('car', 20)),
    h('div', {}, h('b', {}, s.label || 'vehicle'), h('span', { class: 'num' }, fmt.time(s.started_at))));
    pill.addEventListener('animationend', (e) => { if (e.target === pill) pill.remove(); });
    hero.append(pill);
  }

  // ------------------------------------------------------------ right column: camera status

  function tile(cap, ic, value, unit, title = '', warn = false) {
    return h('div', { class: `tile ${warn ? 'warn' : ''}`, title },
      h('span', { class: 'cap' }, cap), h('span', { class: 'tile-ic' }, icon(ic, 18)),
      h('b', {}, value, unit ? h('small', {}, unit) : null));
  }

  function renderStatus(st) {
    const cap = st.capture;
    const s = st.sightings;
    const clip = st.clips.last || {};
    const alive = st.service.state === 'running';
    const [age, unit] = fmt.agoParts(epoch(cap.last_frame_at));
    const [clipAge, clipUnit] = fmt.agoParts(epoch(clip.finished_at));
    const ok = alive && cap.connected;
    const words = (x) => (x || '').replace(/_/g, ' ');
    const det = !s.enabled ? 'off' : s.state === 'running'
      ? (s.cpu_fallback ? 'CPU' : (/intel/i.test(s.device || '') ? 'iGPU' : (s.backend || 'GPU'))) : words(s.state);
    const detLine = s.state === 'running'
      ? `${s.backend || ''} on ${s.device || '?'}${s.phase ? ` (${words(s.phase)})` : ''}; classify queue ${s.classify_queue ?? 0}`
      : (s.last_error || words(s.state));
    const q = st.clips.queue;
    const alerts = [];
    if (st.gaming?.active) alerts.push(h('div', { class: 'alert' }, icon('gamepad', 16), `Gaming: ${st.gaming.exe}`));
    if (q && (q.length || q.deferred)) {
      alerts.push(h('div', { class: 'alert' }, icon('layers', 16),
        `${q.length} render${q.length === 1 ? '' : 's'} queued${q.deferred ? ' · deferred' : ''}`));
    }
    if (s.state === 'crash_looping') alerts.push(h('div', { class: 'alert bad' }, icon('chip', 16), 'Sightings crash-looping'));
    if (!alive) alerts.push(h('div', { class: 'alert bad' }, icon('camera', 16), 'Service stopped · campi start'));
    fill(status,
      cardHead('pin', cap.host || 'campi', h('span', { class: `state-pill ${ok ? 'ok' : 'bad'}` }, ok ? 'Recording' : (alive ? 'No stream' : 'Stopped'))),
      h('div', { class: 'big-row' },
        h('div', { class: 'big num' }, age, h('small', {}, unit)),
        h('div', { class: 'big-cap' }, 'since the', h('br'), 'last frame'),
        h('div', { class: 'grow' }),
        h('span', { class: `glyph ${ok ? '' : 'off'}` }, icon('camera', 44))),
      h('div', { class: 'tiles' },
        tile('Stream', 'wifi', cap.connected ? 'Live' : 'Down', '', cap.last_error || '', !cap.connected),
        tile('Last clip', 'film', clipAge, clipUnit, clip.clip_id || clip.detail || '', clip.status && clip.status !== 'ok'),
        tile('Next clip', 'clock', st.clips.next_at ? fmt.hm(st.clips.next_at) : '–', ''),
        tile('Detector', 'chip', det, '', detLine, s.enabled && s.state !== 'running'),
        tile('Gemini', 'gemini', s.cloud?.enabled ? counter('gemini', s.cloud.calls_today) : 'off', s.cloud?.enabled ? `/${s.cloud.cap}` : '', ''),
        tile('Disk', 'drive', counter('disk', Math.round(st.disk.free_gb)), 'GB', `free on ${st.disk.drive}`, st.disk.free_gb < 60)),
      alerts.length ? h('div', { class: 'alerts' }, alerts) : null);
  }

  // ------------------------------------------------------------ latest sighting (player-style)

  function renderNow(animate) {
    const s = lastSightings[nowIdx];
    if (!s) { fill(latestCard, cardHead('car', 'Latest sighting'), empty('No sightings yet today')); return; }
    const conf = s.decided_by === 'user' ? 1 : (s.confidence ?? 0);
    const fillBar = h('div', { class: 'meter-fill', style: { width: '0%' } });
    const knob = h('div', { class: 'meter-knob', style: { left: '0%' } });
    fill(latestCard,
      cardHead('car', nowIdx === 0 ? 'Latest sighting' : `${nowIdx + 1} sightings ago`, h('a', { class: 'amber-link', href: '#/sightings' }, 'All')),
      h('div', { class: `now-row ${animate ? 'swap' : ''}`, onclick: () => openSighting(s.id, { ids: lastSightings.map((x) => x.id) }) },
        h('div', { class: 'now-thumb' }, s.media.crop ? h('img', { src: s.media.crop, alt: s.label }) : null),
        h('div', { class: 'now-text' }, h('b', {}, s.label || 'vehicle'),
          h('span', {}, [fmt.time(s.started_at), s.year_range, s.color].filter(Boolean).join(' · ')))),
      h('div', { class: 'meter' }, fillBar, knob),
      h('div', { class: 'meter-labels' }, h('span', {}, s.cloud_status === 'pending' ? 'SigLIP · asking Gemini…' : fmt.labelBy(s.decided_by)), h('span', { class: 'num' }, s.decided_by === 'user' ? 'corrected' : fmt.pct(s.confidence))),
      h('div', { class: 'now-ctl' },
        iconButton('prev', 'Older sighting', nowIdx < lastSightings.length - 1 ? () => { nowIdx += 1; renderNow(true); } : null, 'ghost'),
        h('button', { class: 'play-btn big', title: 'View in timelapse', onclick: () => viewInTimelapse(seekTs(s), s.label) }, icon('play', 24)),
        iconButton('next', 'Newer sighting', nowIdx > 0 ? () => { nowIdx -= 1; renderNow(true); } : null, 'ghost')));
    requestAnimationFrame(() => requestAnimationFrame(() => { // let the meter slide to its value
      fillBar.style.width = `${conf * 100}%`;
      knob.style.left = `${conf * 100}%`;
    }));
  }

  function renderMix(t, animate) {
    const by = Object.entries(t.by_class).sort((a, b) => b[1] - a[1]);
    const total = t.total || 1;
    fill(mix,
      cardHead(null, 'Today by class'),
      h('div', { class: 'mix-row' }, h('span', {}, h('i', { class: 'dot' }), counter('mix-total', t.total)), h('span', { class: 'muted' }, 'sightings')),
      h('div', { class: `mix-bar ${animate ? 'anim' : ''}` }, by.map(([k, v], i) => h('span', { class: `seg s${Math.min(i, 3)}`, style: { flexGrow: String(v / total) }, title: `${k} ${v}` }))),
      h('div', { class: 'mix-legend' }, by.map(([k, v], i) => h('span', {}, h('i', { class: `dot s${Math.min(i, 3)}` }), `${k} `, h('b', { class: 'num' }, String(v))))));
  }

  // ------------------------------------------------------------ under the hero: collection ring + chart

  // Collection: how much of the label list has been caught, by rarity tier, plus today's newest catches.
  function renderCollection(c, catches) {
    const ls = listStats(c);
    const animate = lastCaught !== ls.caught;
    lastCaught = ls.caught;
    const segs = tierSegments(c.items);
    const recent = catches.slice(0, 4);
    fill(collCard,
      cardHead('grid', 'Collection', h('a', { class: 'pill-btn', href: '#/collection' }, 'Open')),
      h('div', { class: 'coll-row' },
        ring(segs, ls.total, [h('b', {}, counter('ring-caught', ls.caught)), h('span', { class: 'muted' }, `of ${ls.total} caught`)], { animate }),
        h('div', { class: 'tier-legend' }, segs.map((g) => h('div', { class: 'tl-row' },
          h('i', { class: `tl-dot ${g.key}` }), h('span', {}, g.key), h('b', { class: 'num' }, String(g.value)))),
        h('div', { class: 'tl-row muted' }, h('i', { class: 'tl-dot none' }), h('span', {}, 'not yet'), h('b', { class: 'num' }, String(ls.total - ls.caught))),
        ls.discovered ? h('div', { class: 'tl-row muted' }, h('i', { class: 'tl-dot discovered' }), h('span', {}, 'discovered'), h('b', { class: 'num' }, String(ls.discovered))) : null)),
      h('div', { class: 'catches' },
        h('span', { class: 'catches-title' }, recent.length ? `New today · ${catches.length}` : 'No new catches yet today'),
        h('div', { class: 'catch-row' }, recent.map((i) => h('button', {
          class: 'catch', title: `${i.sighting.label} · first seen ${fmt.hm(i.sighting.started_at)}`, onclick: () => openSighting(i.sighting.id),
        }, i.sighting.media.crop ? h('img', { src: i.sighting.media.crop, alt: '' }) : icon('car', 18), h('span', {}, i.sighting.label))))));
  }

  async function renderChart() {
    let a;
    try { a = await api(`/api/activity?days=${chartMode === 'week' ? 7 : 1}`); } catch { return; }
    if (!alive) return;
    let items = a.items;
    if (a.unit === 'hour') { // from the first hour with sightings (at least 8 bars) to now
      const nowH = new Date().getHours();
      const firstHit = items.findIndex((i) => i.count);
      const from = Math.min(firstHit >= 0 ? firstHit : nowH, Math.max(0, nowH - 7));
      items = items.slice(from, nowH + 1);
    }
    const max = Math.max(1, ...items.map((i) => i.count));
    const total = a.items.reduce((n, i) => n + i.count, 0);
    const busiest = items.reduce((b, i, k) => (i.count > items[b].count ? k : b), 0);
    const sel = picked != null && picked < items.length ? picked : busiest;
    const it = items[sel];
    const label = (i) => (a.unit === 'hour' ? i.start.slice(11, 13) : fmt.weekday(i.start.slice(0, 10)));
    const action = a.unit === 'hour'
      ? h('button', { class: 'pill-btn', onclick: () => viewInTimelapse(epoch(it.start) + 300, `${label(it)}:00`) }, icon('play', 14), 'Play hour')
      : h('a', { class: 'pill-btn', href: `#/sightings?from=${it.start.slice(0, 10)}&to=${it.start.slice(0, 10)}` }, 'Open day');
    const menu = h('select', { class: 'pill-select', onchange: (e) => { chartMode = e.target.value; picked = null; chartAnim = true; renderChart(); } },
      h('option', { value: 'today', selected: chartMode === 'today' }, 'Today'), h('option', { value: 'week', selected: chartMode === 'week' }, 'Week'));
    fill(chart,
      h('header', { class: 'card-head' },
        h('div', {}, h('h2', {}, 'Sightings'),
          h('div', { class: 'chart-sub' }, h('b', {}, counter(`chart-${chartMode}`, total)), h('span', {}, chartMode === 'week' ? 'vehicles in 7 days' : 'vehicles today'))),
        h('div', { class: 'grow' }), menu),
      h('div', { class: 'chart-pick' }, h('span', { class: 'num' }, a.unit === 'hour' ? `${label(it)}:00–${label(it)}:59` : fmt.day(it.start.slice(0, 10))),
        h('b', {}, counter(`pick-${chartMode}-${sel}`, it.count, { ms: 500 })), h('span', { class: 'muted' }, 'sightings'), h('div', { class: 'grow' }), action),
      h('div', { class: `bars ${chartAnim ? 'anim' : ''}` }, items.map((i, k) => h('button', {
        class: `bar ${k === sel ? 'on' : ''}`, title: `${label(i)} · ${i.count}`, style: { '--i': String(k) },
        onclick: () => { picked = k; renderChart(); },
      }, h('span', { class: 'col', style: { height: `${Math.max(4, (i.count / max) * 100)}%`, '--i': String(k) } }, k === sel ? h('i', { class: 'knob' }) : null),
      h('span', { class: 'bar-label num' }, label(i))))));
    chartAnim = false;
  }

  // ------------------------------------------------------------ refresh

  async function refresh() {
    let st;
    try { st = await api('/api/status'); } catch (e) { fill(status, empty('Status unavailable', e.message)); return; }
    if (!alive) return;
    renderStatus(st);
    await newest();
    const on = st.sightings.enabled || st.sightings.has_history;
    fill(banner, offBanner(st));
    for (const n of [latestCard, mix, chart, latest, hl]) n.hidden = !on;
    if (!on) { fill(collCard, sightingsOff()); return; }
    const [t, page, hls, col] = await Promise.all([
      api('/api/today'), api('/api/sightings?limit=12'), api('/api/highlights?limit=200&type=new_catch,rare,busiest,starred'),
      api('/api/collection'),
    ]);
    if (!alive) return;

    // new sightings since the last refresh: drive by (on first load, just the latest one)
    const fresh = page.items.filter((s) => !seenIds.has(s.id));
    const drivers = first ? page.items.slice(0, 1) : fresh.slice(0, MAX_DRIVEBYS);
    drivers.reverse().forEach((s, k) => driveBy(s, 600 + k * 1600));
    const swapped = !lastSightings.length || lastSightings[0].id !== page.items[0]?.id;
    if (swapped) nowIdx = 0;
    lastSightings = page.items;
    renderNow(swapped && !first);
    renderMix(t, first);
    renderChart();

    const ids = page.items.map((s) => s.id);
    const cards = page.items.map((s) => {
      const c = sightingCard(s, () => openSighting(s.id, { ids }));
      if (!seenIds.has(s.id) && !first) { c.classList.add('fresh'); setTimeout(() => c.classList.remove('fresh'), 4000); }
      return c;
    });
    fill(latest, h('header', { class: 'card-head' }, h('span', { class: 'head-ic' }, icon('car', 18)), h('h2', {}, 'Latest sightings'),
      h('div', { class: 'grow' }), h('a', { class: 'amber-link', href: '#/sightings' }, 'All sightings')),
    page.items.length ? h('div', { class: 'grid' }, cards) : empty('No sightings yet today'));
    stagger(first ? cards : cards.filter((c) => !seenIds.has(c.dataset.id)));
    page.items.forEach((s) => seenIds.add(s.id));

    const todayStart = new Date(`${fmt.localDate()}T00:00:00`).getTime();
    const todays = hls.items.filter((i) => new Date(i.at).getTime() >= todayStart);
    const catches = todays.filter((i) => i.types.includes('new_catch') && i.sighting);
    const newCatches = catches.filter((i) => !seenCatches.has(i.sighting.id));
    if (!first && newCatches.length) { // a first-ever label just showed up: party
      confetti();
      toast(`New catch! ${newCatches.map((i) => i.sighting.label).join(', ')}`, 'party');
    }
    catches.forEach((i) => seenCatches.add(i.sighting.id));
    renderCollection(col, catches);
    const hlCards = todays.slice(0, 24).map((i) => highlightCard(i));
    fill(hl, h('header', { class: 'card-head' }, h('span', { class: 'head-ic' }, icon('sparkle', 18)), h('h2', {}, "Today's highlights"),
      h('div', { class: 'grow' }), badge(`${todays.length}`, 'amber'), h('a', { class: 'amber-link', href: '#/highlights' }, 'All highlights')),
    todays.length ? h('div', { class: 'grid' }, hlCards) : empty('Nothing special yet today'));
    if (first) stagger(hlCards);
    selection.set([...el.querySelectorAll('.card.sighting, .card.hl')], {
      open: (n) => n.click(),
      star: (n) => n.querySelector('.star')?.click(),
    });
    first = false;
  }

  await refresh();
  const timer = setInterval(refresh, REFRESH_MS);
  return {
    unmount() { alive = false; clearInterval(timer); releaseVideo(video); },
    video: () => video,
  };
}
