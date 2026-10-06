import {
  api, archiveFile, badge, cardHead, clipFile, dailyFile, empty, epoch, fill, fmt, h, icon, iconButton, player, post, prefs,
  releaseVideo, revealButton, selection, stagger, starButton, tabs, toast,
} from '../lib.js';

// every daily video (the list is paged; a few hundred at most)
async function allDaily() {
  const items = [];
  let cursor = null;
  do {
    const p = await api(`/api/daily?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    items.push(...p.items);
    cursor = p.next_cursor;
  } while (cursor);
  return { items };
}

const pad = (n) => String(n).padStart(2, '0');
const toLocalInput = (d) => `${fmt.localDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const SPEEDS = [60, 300, 900, 3600];
const LENGTHS = [15, 30, 60, 120];
const PRESETS = [['1h', 'Last hour'], ['6h', 'Last 6 h'], ['24h', 'Last 24 h'], ['today', 'Today'], ['yesterday', 'Yesterday']];

function presetRange(key) {
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (key === 'today') return [midnight, now];
  if (key === 'yesterday') return [new Date(midnight.getTime() - 86400e3), midnight];
  const hours = { '1h': 1, '6h': 6, '24h': 24 }[key];
  return [new Date(now.getTime() - hours * 3600e3), now];
}

function span(s) {
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400 * 2) return `${+(s / 3600).toFixed(1)} h`;
  return `${+(s / 86400).toFixed(1)} days`;
}
const speedText = (x) => `${x >= 100 ? Math.round(x) : +x.toFixed(1)}×`;
const lengthText = (s) => (s < 59.5 ? `${Math.round(s)} s` : `${Math.floor(Math.round(s) / 60)}:${pad(Math.round(s) % 60)}`);

export async function mount(el) {
  const stage = h('section', { class: 'panel stage' });
  const makeSec = h('section', { class: 'panel make' });
  const customSec = h('section', { class: 'panel' });
  const clipsSec = h('section', { class: 'panel' });
  const dailySec = h('section', { class: 'panel' });
  const archSec = h('section', { class: 'panel' });
  el.append(stage, makeSec, customSec, clipsSec, dailySec, archSec);
  let video = null;
  let alive = true;
  let poll = null;
  let current = null; // file name playing in the stage
  let customOrder = []; // [{root, item, title}] for the custom section (rebuilt on every refresh)
  const fixedOrder = []; // clips, daily, archive in display order
  const order = () => customOrder.concat(fixedOrder);

  function play(entry) {
    const { root, item, title } = entry;
    releaseVideo(video);
    current = item.file;
    video = h('video', { src: item.media.video, autoplay: true, preload: 'auto' });
    video.addEventListener('error', () => toast('This video is no longer available (it may have expired).', 'error'));
    const step = (d) => {
      const list = order();
      for (let j = list.findIndex((o) => o.item.file === current) + d; j >= 0 && j < list.length; j += d) {
        if (list[j].item.media.video) return list[j];
      }
      return null;
    };
    const go = (d) => (step(d) ? () => { const next = step(d); if (next) play(next); } : null);
    fill(stage, cardHead('film', title, revealButton(root, item.file, true)), player(video, { onPrev: go(-1), onNext: go(1) }));
    markPlaying();
    stage.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  function markPlaying() {
    el.querySelectorAll('.vid.playing').forEach((n) => n.classList.remove('playing'));
    if (current) el.querySelector(`.vid[data-name="${current}"]`)?.classList.add('playing');
  }
  function refreshSelection() {
    selection.set([...el.querySelectorAll('.vid')], {
      open: (n) => n.click(),
      star: (n) => n.querySelector('.star')?.click(),
    });
  }

  const [clips, daily, archive, info] = await Promise.all([
    api('/api/clips').catch(() => ({ items: [] })), allDaily().catch(() => ({ items: [] })), api('/api/archive').catch(() => ({ items: [] })),
    api('/api/desktop/info').catch(() => ({})),
  ]);
  if (!alive) return { unmount() {} };

  const vid = (entry, cls, top, big, sub, actions, extra = {}) => h('div', {
    class: `vid ${cls}`, dataset: { name: entry.item.file }, tabindex: '-1', onclick: () => (entry.item.media.video ? play(entry) : extra.locked?.()), title: extra.title || '',
  },
  h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, top), h('span', { class: 'tile-ic' }, icon(extra.icon || 'film', 18))),
  h('b', { class: 'tc-big num' }, big),
  h('div', { class: 'tc-sub' }, sub, h('span', { class: 'grow' }), actions),
  extra.progress != null ? h('div', { class: 'vid-progress' }, h('i', { style: { width: `${Math.round(extra.progress * 100)}%` } })) : null);
  const add = (root, item, title) => { const e = { root, item, title }; fixedOrder.push(e); return e; };

  // ------------------------------------------------------------ make a custom timelapse

  const baseFps = info.base_fps || 30;
  const native = (info.interval_s || 2) * baseFps; // speed when every captured frame is used
  const thinnedMax = 60 * baseFps; // older than raw_hours: about 1 frame a minute is kept
  const [minLen, maxLen] = info.custom_seconds || [2, 600];
  const maxRange = (info.custom_max_range_h || 168) * 3600;
  const form = {
    preset: prefs.get('tl.preset', '1h'), mode: prefs.get('tl.mode', 'speed'),
    speed: prefs.get('tl.speed', 300), seconds: prefs.get('tl.seconds', 30),
  };
  const fromIn = h('input', { type: 'datetime-local', step: 60 });
  const toIn = h('input', { type: 'datetime-local', step: 60 });
  const numIn = h('input', { type: 'number', min: 1, step: 1, class: 'num' });
  const numUnit = h('span', {});
  const presetRow = h('div', {});
  const modeRow = h('div', {});
  const chipRow = h('div', {});
  const summary = h('div', { class: 'make-summary' });
  const renderBtn = h('button', { class: 'primary' }, icon('sparkle', 18), 'Render');

  function applyPreset(key) {
    form.preset = key;
    prefs.set('tl.preset', key);
    const [a, b] = presetRange(key);
    fromIn.value = toLocalInput(a);
    toIn.value = toLocalInput(b);
    draw();
  }
  function range() {
    const a = fromIn.value ? new Date(fromIn.value).getTime() / 1000 : NaN;
    const b = toIn.value ? new Date(toIn.value).getTime() / 1000 : NaN;
    return [a, b];
  }
  function value() { return form.mode === 'speed' ? form.speed : form.seconds; }
  function check() {
    const [a, b] = range();
    const v = value();
    if (!Number.isFinite(a) || !Number.isFinite(b)) return { err: 'Pick a start and an end.' };
    if (b <= a) return { err: 'The end must be after the start.' };
    if (b > Date.now() / 1000 + 60) return { err: "The end can't be in the future." };
    if (b - a > maxRange) return { err: `The range can be at most ${span(maxRange)}.` };
    if (!(v > 0)) return { err: form.mode === 'speed' ? 'Enter a speed.' : 'Enter a length.' };
    const out = form.mode === 'speed' ? (b - a) / v : v;
    if (out > maxLen) return { err: `That would be ${lengthText(out)} long; the most is ${maxLen / 60} min (try ${speedText((b - a) / maxLen)} or faster).` };
    if (out < minLen) return { err: `That would be under ${minLen} s long; try ${speedText((b - a) / minLen)} or slower.` };
    return { a, b, out, speed: (b - a) / out };
  }
  function draw() {
    fill(presetRow, tabs(PRESETS, (k) => k === form.preset, applyPreset));
    fill(modeRow, tabs([['speed', 'Speed'], ['seconds', 'Length']], (k) => k === form.mode, (k) => {
      form.mode = k;
      prefs.set('tl.mode', k);
      draw();
    }));
    const opts = form.mode === 'speed' ? SPEEDS : LENGTHS;
    fill(chipRow, tabs(opts.map((o) => [o, form.mode === 'speed' ? speedText(o) : `${o} s`]), (o) => o === value(), (o) => {
      form[form.mode] = o;
      prefs.set(`tl.${form.mode}`, o);
      draw();
    }));
    if (document.activeElement !== numIn) numIn.value = value();
    numUnit.textContent = form.mode === 'speed' ? '× real time' : 'seconds';
    const c = check();
    renderBtn.disabled = !!c.err;
    if (c.err) { fill(summary, h('span', { class: 'bad' }, c.err)); return; }
    const notes = [];
    const thinnedFrom = Date.now() / 1000 - (info.raw_hours || 48) * 3600;
    if (c.a < thinnedFrom && c.speed < thinnedMax) {
      notes.push(`Frames older than ${info.raw_hours || 48} h are thinned to about one a minute, so that part plays at about ${speedText(thinnedMax)} (shorter than asked).`);
    } else if (c.speed < native) {
      notes.push(`The camera saves a frame every ${info.interval_s || 2} s, so it can't go slower than ${speedText(native)}: the video will be about ${lengthText((c.b - c.a) / native)} long.`);
    }
    fill(summary,
      h('b', { class: 'num' }, `${span(c.b - c.a)} → ${lengthText(c.out)} video at ${speedText(c.speed)}`),
      notes.map((n) => h('span', { class: 'muted' }, n)));
  }
  numIn.addEventListener('input', () => {
    const v = Number(numIn.value);
    form[form.mode] = v;
    if (v > 0) prefs.set(`tl.${form.mode}`, v);
    draw();
  });
  for (const inp of [fromIn, toIn]) inp.addEventListener('input', () => { form.preset = null; draw(); });
  renderBtn.addEventListener('click', async () => {
    const c = check();
    if (c.err) return;
    renderBtn.disabled = true;
    try {
      await post('/api/desktop/renders', {
        start: new Date(c.a * 1000).toISOString(), end: new Date(c.b * 1000).toISOString(),
        ...(form.mode === 'speed' ? { speed: form.speed } : { seconds: form.seconds }),
      });
      toast('Queued: the PC renders it in the background.');
      await refreshCustom();
    } catch (err) { toast(err.message, 'error'); } finally { draw(); }
  });

  fill(makeSec, cardHead('sparkle', 'Make a timelapse'),
    h('div', { class: 'make-grid' },
      h('label', { class: 'make-label' }, 'Range'),
      h('div', { class: 'make-row' }, h('label', { class: 'pill-field' }, 'From', fromIn), h('label', { class: 'pill-field' }, 'To', toIn)),
      h('span', {}), presetRow,
      h('label', { class: 'make-label' }, 'Speed'),
      h('div', { class: 'make-row' }, modeRow, chipRow, h('label', { class: 'pill-field' }, numIn, numUnit)),
      h('span', {}), h('div', { class: 'make-row end' }, summary, renderBtn)));
  if (form.preset) applyPreset(form.preset); else applyPreset('1h');

  // ------------------------------------------------------------ custom timelapses

  let known = null; // id -> status, to tell when one finishes
  // Stop (rendering), Cancel (queued) or Delete (finished): two clicks. The first arms it for a few seconds; that is
  // kept per job, not on the button, because the section redraws every 3 s while a render runs.
  const armedAt = new Map();
  const ARM_MS = 5000;
  function removeButton(item) {
    const active = item.status === 'queued' || item.status === 'rendering';
    const verb = !active ? 'Delete' : item.status === 'queued' ? 'Cancel' : 'Stop';
    const armed = () => Date.now() - (armedAt.get(item.id) || 0) < ARM_MS;
    const label = h('span', {});
    const b = active
      ? h('button', { class: 'stop-btn', title: `${verb} this render (nothing is saved)` }, icon('x', 14), label)
      : iconButton('x', 'Delete', null, 'small');
    const paint = () => {
      b.classList.toggle('armed', armed());
      if (active) label.textContent = armed() ? `Click to ${verb.toLowerCase()}` : verb;
      else b.title = armed() ? 'Click again to delete' : 'Delete';
    };
    const unarmLater = () => setTimeout(() => { if (b.isConnected && !b.disabled) paint(); },
      ARM_MS - (Date.now() - armedAt.get(item.id)) + 50);
    b.disabled = false;
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!armed()) {
        armedAt.set(item.id, Date.now());
        paint();
        toast(active ? `Click ${verb} again to ${verb.toLowerCase()} this render. Nothing it has made so far is kept.`
          : 'Click again to delete this timelapse.');
        unarmLater();
        return;
      }
      armedAt.delete(item.id);
      b.disabled = true;
      b.classList.remove('armed');
      if (active) label.textContent = verb === 'Stop' ? 'Stopping…' : 'Cancelling…';
      try {
        await post(`/api/desktop/renders/${item.id}/delete`, {});
        if (current === item.file) { releaseVideo(video); video = null; current = null; emptyStage(); }
        known?.delete(item.id);
        toast(verb === 'Stop' ? 'Render stopped. Nothing was saved.' : verb === 'Cancel' ? 'Render cancelled.' : 'Timelapse deleted.');
        await refreshCustom();
      } catch (err) {
        b.disabled = false;
        paint();
        toast(err.message, 'error');
      }
    });
    paint();
    if (armed()) unarmLater(); // redrawn while armed: still go back to the plain label on time
    return b;
  }
  // "42% · 1:20 in · ~2 min left" (the estimate once there's enough progress to go on)
  function renderingText(c) {
    const p = c.progress || 0;
    const ran = c.started ? Math.max(0, Date.now() / 1000 - epoch(c.started)) : null;
    const parts = [fmt.pct(p)];
    if (ran != null) parts.push(`${lengthText(ran)} in`);
    if (ran != null && p >= 0.05 && p < 1) {
      const left = (ran * (1 - p)) / p;
      parts.push(left < 60 ? '<1 min left' : `~${Math.round(left / 60)} min left`);
    }
    return parts.join(' · ');
  }
  function customTile(c) {
    c.file = c.file || `custom-${c.id}`; // unique tile key until there's a video
    const sameDay = c.window_start.slice(0, 10) === c.window_end.slice(0, 10);
    const when = sameDay ? fmt.day(c.window_start.slice(0, 10)) : `${fmt.day(c.window_start.slice(0, 10))} → ${fmt.day(c.window_end.slice(0, 10))}`;
    const asked = c.speed ? speedText(c.speed) : `${c.seconds} s`;
    const entry = { root: 'custom', item: c, title: `Custom · ${when} · ${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}` };
    let sub; let extra = { icon: 'sparkle' };
    if (c.status === 'ok') {
      customOrder.push(entry);
      sub = h('span', { class: 'muted num' }, `${speedText(c.actual_speed || c.speed || 0)} · ${lengthText(c.duration_s || 0)}`);
      extra = { ...extra, title: fmt.size(c.size_bytes) };
    } else if (c.status === 'queued' || c.status === 'rendering') {
      sub = h('span', { class: 'muted num' }, c.status === 'queued' ? `queued · ${asked}` : renderingText(c));
      extra = { ...extra, progress: c.progress || 0, locked: () => toast('Still rendering; it plays here when it\'s done.') };
    } else {
      sub = h('span', { class: 'bad' }, c.status);
      extra = { ...extra, title: c.reason || '', locked: () => toast(c.reason || `This render ${c.status}.`, 'error') };
    }
    const actions = c.status === 'ok'
      ? [starButton(c.starred, (v) => post(`/api/desktop/renders/${c.id}/star`, { starred: v }).then(() => { c.starred = v; })),
        revealButton('custom', c.file), removeButton(c)]
      : [removeButton(c)];
    return vid(entry, `custom ${c.status === 'ok' ? '' : `pending ${c.status}`}`, when,
      `${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`, sub, actions, extra);
  }
  async function refreshCustom(first = false) {
    clearTimeout(poll);
    let res;
    try { res = await api('/api/desktop/renders'); } catch { res = { items: [], service_running: true }; }
    if (!alive) return;
    const active = res.items.some((c) => c.status === 'queued' || c.status === 'rendering');
    if (known) {
      for (const c of res.items) {
        const was = known.get(c.id);
        if (was && was !== c.status && c.status === 'ok') toast('Your custom timelapse is ready.');
        if (was && was !== c.status && (c.status === 'failed' || c.status === 'skipped')) toast(`Custom timelapse ${c.status}: ${c.reason || ''}`, 'error');
      }
    }
    known = new Map(res.items.map((c) => [c.id, c.status]));
    customOrder = [];
    const note = !res.service_running && active ? badge('service stopped: renders wait until it runs', 'warn')
      : res.renders_deferred && active ? badge('waiting: gaming', 'warn') : null;
    fill(customSec, cardHead('sparkle', 'Custom', note, badge(String(res.items.length), 'muted')));
    if (!res.items.length) customSec.append(empty('No custom timelapses yet', 'Pick a range and a speed above.'));
    else customSec.append(h('div', { class: 'vids wide-vids' }, res.items.map(customTile)));
    if (first) stagger([...customSec.querySelectorAll('.vid')]);
    markPlaying();
    refreshSelection();
    if (active) poll = setTimeout(() => refreshCustom(), 3000);
  }

  // ------------------------------------------------------------ 10-minute clips, grouped by hour (newest hour first)

  clips.items.forEach((c) => { c.file = clipFile(c.id); });
  daily.items.forEach((d) => { d.file = dailyFile(d.day); });
  archive.items.forEach((a) => { a.file = archiveFile(a.part); });
  const onGrid = (c) => c.window_start.slice(17, 23) === '00.000' && Number(c.window_start.slice(14, 16)) % (clips.window_min || 10) === 0;
  const hours = new Map();
  for (const c of clips.items) {
    const key = c.window_start.slice(0, 13);
    if (!hours.has(key)) hours.set(key, []);
    hours.get(key).push(c);
  }
  clipsSec.append(cardHead('clock', '10-minute clips', badge(`${clips.items.length} · last ${clips.retention_hours ?? 24} h`, 'muted')));
  if (!clips.items.length) clipsSec.append(empty(`No clips in the last ${clips.retention_hours ?? 24} hours`));
  for (const [key, list] of hours) {
    clipsSec.append(h('div', { class: 'hour' },
      h('div', { class: 'hour-head' }, h('b', { class: 'num' }, `${key.slice(11)}:00`), h('span', { class: 'muted' }, fmt.day(key.slice(0, 10)))),
      h('div', { class: 'vids' }, list.slice().reverse().map((c) => {
        const e = add('clips', c, `${fmt.day(c.day)} · ${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`);
        return vid(e, '', onGrid(c) ? `${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}` : 'manual',
          c.sightings_count != null ? [String(c.sightings_count), h('small', {}, 'seen')] : fmt.hm(c.window_start),
          h('span', { class: 'muted num' }, c.sightings_count != null ? fmt.hm(c.window_start) : fmt.size(c.size_bytes)),
          [starButton(c.starred, (v) => post(`/api/clips/${c.id}/star`, { starred: v }).then(() => { c.starred = v; })), revealButton('clips', c.file)]);
      }))));
  }

  dailySec.append(cardHead('calendar', 'Daily videos', badge(String(daily.items.length), 'muted')));
  if (!daily.items.length) dailySec.append(empty('No daily videos yet', 'Each day is rendered shortly after midnight.'));
  dailySec.append(h('div', { class: 'vids' }, daily.items.map((d) => {
    const e = add('daily', d, `Day · ${fmt.day(d.day)}`);
    return vid(e, 'wide', fmt.weekday(d.day), d.day.slice(5), h('span', { class: 'muted num' }, fmt.size(d.size_bytes)),
      [starButton(d.starred, (v) => post(`/api/daily/${d.day}/star`, { starred: v }).then(() => { d.starred = v; })), revealButton('daily', d.file)],
      { icon: 'calendar' });
  })));

  archSec.append(cardHead('layers', 'Archive', badge(`${archive.items.length} part${archive.items.length === 1 ? '' : 's'}`, 'muted')));
  if (!archive.items.length) archSec.append(empty('No archive yet'));
  archSec.append(h('div', { class: 'vids' }, archive.items.map((a) => {
    const e = add('archive', a, `Archive part ${a.part}`);
    return vid(e, `wide ${a.current ? 'disabled' : ''}`, a.current ? 'being written' : `updated ${fmt.date(a.modified_at).slice(5)}`,
      `Part ${a.part}`, h('span', { class: 'muted num' }, fmt.size(a.size_bytes)), revealButton('archive', a.file),
      {
        icon: 'layers',
        title: a.current ? 'Still being appended to every 10 minutes; playable once the next part starts' : '',
        locked: () => toast('This part is still being written. It becomes playable once the next part starts.'),
      });
  })));

  function emptyStage() {
    fill(stage, cardHead('film', 'Player'), h('div', { class: 'stage-empty' }, icon('play', 40), h('p', { class: 'muted' }, 'Pick a clip, a day or an archive part below, or make your own.')));
  }
  emptyStage();
  stagger([...el.querySelectorAll('.vid')]);
  await refreshCustom(true);
  return {
    unmount() { alive = false; clearTimeout(poll); releaseVideo(video); },
    video: () => video,
  };
}
