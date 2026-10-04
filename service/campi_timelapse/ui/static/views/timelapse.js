import {
  api, archiveFile, badge, cardHead, clipFile, dailyFile, empty, fill, fmt, h, icon, player, post, releaseVideo, revealButton,
  selection, stagger, starButton, toast,
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

export async function mount(el) {
  const stage = h('section', { class: 'panel stage' });
  const clipsSec = h('section', { class: 'panel' });
  const dailySec = h('section', { class: 'panel' });
  const archSec = h('section', { class: 'panel' });
  el.append(stage, clipsSec, dailySec, archSec);
  let video = null;
  let alive = true;
  const order = []; // [{root, item, title}] in display order, for prev / next

  function play(k) {
    const entry = order[k];
    if (!entry) return;
    const { root, item, title } = entry;
    releaseVideo(video);
    video = h('video', { src: item.media.video, autoplay: true, preload: 'auto' });
    video.addEventListener('error', () => toast('This video is no longer available (it may have expired).', 'error'));
    const step = (d) => { for (let j = k + d; j >= 0 && j < order.length; j += d) if (order[j].item.media.video) return () => play(j); return null; };
    fill(stage, cardHead('film', title, revealButton(root, item.file, true)), player(video, { onPrev: step(-1), onNext: step(1) }));
    el.querySelectorAll('.vid.playing').forEach((n) => n.classList.remove('playing'));
    el.querySelector(`.vid[data-name="${item.file}"]`)?.classList.add('playing');
    stage.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  const [clips, daily, archive] = await Promise.all([
    api('/api/clips').catch(() => ({ items: [] })), allDaily().catch(() => ({ items: [] })), api('/api/archive').catch(() => ({ items: [] })),
  ]);
  if (!alive) return { unmount() {} };

  const add = (root, item, title) => { order.push({ root, item, title }); return order.length - 1; };
  const vid = (k, item, cls, top, big, sub, actions, extra = {}) => h('div', {
    class: `vid ${cls}`, dataset: { name: item.file }, tabindex: '-1', onclick: () => (item.media.video ? play(k) : extra.locked?.()), title: extra.title || '',
  },
  h('div', { class: 'tc-top' }, h('span', { class: 'cap' }, top), h('span', { class: 'tile-ic' }, icon(extra.icon || 'film', 18))),
  h('b', { class: 'tc-big num' }, big),
  h('div', { class: 'tc-sub' }, sub, h('span', { class: 'grow' }), actions));

  // 10-minute clips, grouped by hour (newest hour first)
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
        const k = add('clips', c, `${fmt.day(c.day)} · ${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}`);
        return vid(k, c, '', onGrid(c) ? `${fmt.hm(c.window_start)}–${fmt.hm(c.window_end)}` : 'manual',
          c.sightings_count != null ? [String(c.sightings_count), h('small', {}, 'seen')] : fmt.hm(c.window_start),
          h('span', { class: 'muted num' }, c.sightings_count != null ? fmt.hm(c.window_start) : fmt.size(c.size_bytes)),
          [starButton(c.starred, (v) => post(`/api/clips/${c.id}/star`, { starred: v }).then(() => { c.starred = v; })), revealButton('clips', c.file)]);
      }))));
  }

  dailySec.append(cardHead('calendar', 'Daily videos', badge(String(daily.items.length), 'muted')));
  if (!daily.items.length) dailySec.append(empty('No daily videos yet', 'Each day is rendered shortly after midnight.'));
  dailySec.append(h('div', { class: 'vids' }, daily.items.map((d) => {
    const k = add('daily', d, `Day · ${fmt.day(d.day)}`);
    return vid(k, d, 'wide', fmt.weekday(d.day), d.day.slice(5), h('span', { class: 'muted num' }, fmt.size(d.size_bytes)),
      [starButton(d.starred, (v) => post(`/api/daily/${d.day}/star`, { starred: v }).then(() => { d.starred = v; })), revealButton('daily', d.file)],
      { icon: 'calendar' });
  })));

  archSec.append(cardHead('layers', 'Archive', badge(`${archive.items.length} part${archive.items.length === 1 ? '' : 's'}`, 'muted')));
  if (!archive.items.length) archSec.append(empty('No archive yet'));
  archSec.append(h('div', { class: 'vids' }, archive.items.map((a) => {
    const k = add('archive', a, `Archive part ${a.part}`);
    return vid(k, a, `wide ${a.current ? 'disabled' : ''}`, a.current ? 'being written' : `updated ${fmt.date(a.modified_at).slice(5)}`,
      `Part ${a.part}`, h('span', { class: 'muted num' }, fmt.size(a.size_bytes)), revealButton('archive', a.file),
      {
        icon: 'layers',
        title: a.current ? 'Still being appended to every 10 minutes; playable once the next part starts' : '',
        locked: () => toast('This part is still being written. It becomes playable once the next part starts.'),
      });
  })));

  fill(stage, cardHead('film', 'Player'), h('div', { class: 'stage-empty' }, icon('play', 40), h('p', { class: 'muted' }, 'Pick a clip, a day or an archive part below.')));
  stagger([...el.querySelectorAll('.vid')]);
  selection.set([...el.querySelectorAll('.vid')], {
    open: (n) => n.click(),
    star: (n) => n.querySelector('.star')?.click(),
  });
  return {
    unmount() { alive = false; releaseVideo(video); },
    video: () => video,
  };
}
