import { fill, api, badge, empty, fmt, h, post, revealButton, selection, starButton, toast } from '../lib.js';

export async function mount(el) {
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Timelapse')));
  const stage = h('section', { class: 'panel stage' });
  const clipsSec = h('section', { class: 'panel' });
  const dailySec = h('section', { class: 'panel' });
  const archSec = h('section', { class: 'panel' });
  el.append(stage, clipsSec, dailySec, archSec);
  let video = null;
  let alive = true;

  function play(root, item, title) {
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    video = h('video', { src: item.url, controls: true, autoplay: true, playsinline: true, preload: 'auto' });
    video.addEventListener('error', () => toast('This video is no longer available (it may have expired).', 'error'));
    fill(stage, h('header', { class: 'row' }, h('h2', {}, title), h('div', { class: 'grow' }), revealButton(root, item.name)), video);
    el.querySelectorAll('.vid.playing').forEach((n) => n.classList.remove('playing'));
    el.querySelector(`.vid[data-name="${item.name}"]`)?.classList.add('playing');
    stage.scrollIntoView({ block: 'nearest' });
  }

  const [clips, daily, archive] = await Promise.all([
    api('/api/clips').catch(() => ({ items: [] })), api('/api/daily').catch(() => ({ items: [] })), api('/api/archive').catch(() => ({ items: [] })),
  ]);
  if (!alive) return { unmount() {} };

  // last 24 h of 10-minute clips, grouped by hour (newest first)
  const hours = new Map();
  for (const c of clips.items) {
    const key = c.start_local.slice(0, 13);
    if (!hours.has(key)) hours.set(key, []);
    hours.get(key).push(c);
  }
  clipsSec.append(h('h2', {}, `10-minute clips · last 24 h (${clips.items.length})`));
  if (!clips.items.length) clipsSec.append(empty('No clips in the last 24 hours'));
  for (const [key, list] of hours) {
    clipsSec.append(h('div', { class: 'hour' },
      h('div', { class: 'hour-head' }, `${fmt.day(key.slice(0, 10))} ${key.slice(11)}:00`),
      h('div', { class: 'vids' }, list.slice().reverse().map((c) => h('div', {
        class: 'vid', dataset: { name: c.name }, tabindex: '-1',
        onclick: () => play('clips', c, `${fmt.day(fmt.date(c.start_local))} ${fmt.hm(c.start_local)}–${fmt.hm(c.end_local)}`),
      },
      h('div', { class: 'vid-time' }, fmt.hm(c.start_local), c.on_grid ? null : badge('manual', 'muted')),
      h('div', { class: 'vid-sub' }, c.sightings != null ? `${c.sightings} sightings` : fmt.size(c.size)),
      h('div', { class: 'vid-actions' }, starButton(c.starred, (v) => post(`/api/videos/clips/${c.name}/star`, { value: v }).then(() => { c.starred = v; })), revealButton('clips', c.name)))))));
  }

  dailySec.append(h('h2', {}, `Daily videos (${daily.items.length})`));
  if (!daily.items.length) dailySec.append(empty('No daily videos yet', 'Each day is rendered shortly after midnight.'));
  dailySec.append(h('div', { class: 'vids' }, daily.items.map((d) => h('div', {
    class: 'vid wide', dataset: { name: d.name }, tabindex: '-1', onclick: () => play('daily', d, `Day ${fmt.day(d.day)}`),
  }, h('div', { class: 'vid-time' }, fmt.day(d.day)), h('div', { class: 'vid-sub' }, fmt.size(d.size)),
  h('div', { class: 'vid-actions' }, starButton(d.starred, (v) => post(`/api/videos/daily/${d.name}/star`, { value: v }).then(() => { d.starred = v; })), revealButton('daily', d.name))))));

  archSec.append(h('h2', {}, 'Archive'));
  if (!archive.items.length) archSec.append(empty('No archive yet'));
  archSec.append(h('div', { class: 'vids' }, archive.items.map((a) => h('div', {
    class: `vid wide ${a.current ? 'disabled' : ''}`, dataset: { name: a.name }, tabindex: '-1',
    title: a.current ? 'Still being appended to every 10 minutes; playable once the next part starts' : '',
    onclick: () => (a.url ? play('archive', a, `Archive part ${a.part}`) : toast('This part is still being written. It becomes playable once the next part starts.')),
  }, h('div', { class: 'vid-time' }, `Part ${a.part}`, a.current ? badge('being written', 'warn') : null),
  h('div', { class: 'vid-sub' }, `${fmt.size(a.size)} · updated ${fmt.dateTime(a.modified_local)}`),
  h('div', { class: 'vid-actions' }, revealButton('archive', a.name))))));

  stage.append(h('h2', {}, 'Player'), h('p', { class: 'muted' }, 'Pick a clip, a day or an archive part below.'));
  selection.set([...el.querySelectorAll('.vid')], {
    open: (n) => n.click(),
    star: (n) => n.querySelector('.star')?.click(),
  });
  return {
    unmount() { alive = false; if (video) { video.pause(); video.removeAttribute('src'); video.load(); } },
    video: () => video,
  };
}
