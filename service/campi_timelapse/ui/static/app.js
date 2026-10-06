// Shell: hamburger nav, top bar, router, keyboard and visibility handling.
// Each view module exports mount(el, params, ctx) -> {unmount, onVisible?, video?}.
import {
  $, api, closeDetail, closeModal, detailOpen, detailStep, detailVideo, epoch, fill, fmt, h, icon, iconButton, modalVideo,
  prefs, reduceMotion, selection, starDetail, stepRate, toast,
} from './lib.js';
import * as today from './views/today.js';
import * as highlights from './views/highlights.js';
import * as sightings from './views/sightings.js';
import * as collection from './views/collection.js';
import * as cards from './views/cards.js';
import * as timelapse from './views/timelapse.js';
import * as live from './views/live.js';

const VIEWS = { today, highlights, sightings, collection, cards, timelapse, live };
const TITLES = {
  today: 'Today', highlights: 'Highlights', sightings: 'Sightings', collection: 'Collection', cards: 'Cards', timelapse: 'Timelapse', live: 'Live',
};
let current = null; // {name, handle}
let hidden = document.hidden;

// ---------------------------------------------------------------- nav (hamburger)

const narrow = window.matchMedia('(max-width: 980px)');
function setCollapsed(c, remember = true) {
  document.body.classList.toggle('nav-collapsed', c);
  $('#hamburger').title = c ? 'Expand menu' : 'Collapse menu';
  if (remember) prefs.set('navCollapsed', c);
  moveIndicator();
  setTimeout(moveIndicator, 320); // after the width transition
}

// the active pill slides between nav items
function moveIndicator() {
  const a = $('.nav-links a.active');
  const ind = $('.nav-ind');
  if (!a || !ind) return;
  ind.style.transform = `translateY(${a.offsetTop}px)`;
  ind.style.width = `${a.offsetWidth}px`;
}
fill($('#hamburger'), icon('menu', 20));
$('#hamburger').addEventListener('click', () => setCollapsed(!document.body.classList.contains('nav-collapsed')));
document.querySelectorAll('.nav a').forEach((a) => a.prepend(icon(a.dataset.icon, 20)));
setCollapsed(prefs.get('navCollapsed', false) || narrow.matches, false);
narrow.addEventListener('change', (e) => setCollapsed(e.matches || prefs.get('navCollapsed', false), false));

// ---------------------------------------------------------------- top bar: refresh, live, camera chip

const chip = h('div', { class: 'cam-chip', title: '' });
fill($('#top-actions'),
  iconButton('refresh', 'Refresh', (e) => {
    const b = e.currentTarget;
    b.classList.remove('spin'); void b.offsetWidth; b.classList.add('spin');
    route();
  }),
  iconButton('live', 'Live view', () => { location.hash = '#/live'; }),
  chip);

async function updateChip() {
  let st;
  try { st = await api('/api/status'); } catch { fill(chip, h('span', { class: 'cam-av bad' }, icon('camera', 20)), h('div', {}, h('b', {}, 'Campi'), h('span', {}, 'UI server unreachable'))); return; }
  const cap = st.capture;
  const alive = st.service.state === 'running';
  const ok = alive && cap.connected;
  chip.title = ok ? `Service pid ${st.service.pid}; stream ${cap.host}` : (alive ? (cap.last_error || 'stream disconnected') : 'service stopped (campi start)');
  fill(chip,
    h('span', { class: `cam-av ${ok ? 'ok' : 'bad'}` }, icon('camera', 20)),
    h('div', {}, h('b', {}, cap.host || 'Campi'),
      h('span', {}, alive ? (cap.connected ? `recording · frame ${fmt.ago(epoch(cap.last_frame_at))}` : 'stream disconnected') : 'service stopped')));
}
updateChip();
setInterval(updateChip, 30000);

// ---------------------------------------------------------------- router

function parseHash() {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  return { name: VIEWS[path] ? path : 'today', params: Object.fromEntries(new URLSearchParams(query)) };
}

async function route() {
  const { name, params } = parseHash();
  closeModal();
  closeDetail();
  selection.clear();
  if (current) {
    try { current.handle?.unmount?.(); } catch (e) { console.error(e); }
  }
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === name));
  moveIndicator();
  const title = $('#page-title');
  title.textContent = TITLES[name];
  title.classList.remove('swap'); void title.offsetWidth; title.classList.add('swap');
  $('#page-sub').textContent = fmt.day(fmt.localDate());
  document.title = `Campi · ${TITLES[name]}`;
  const main = $('#main');
  main.replaceChildren();
  main.scrollTop = 0;
  main.dataset.view = name;
  current = { name, handle: null };
  const mine = current;
  const handle = await VIEWS[name].mount(main, params, { isHidden: () => hidden });
  if (current === mine) current.handle = handle; else handle?.unmount?.(); // navigated away while mounting
}

function setHidden(v) {
  if (v === hidden) return;
  hidden = v;
  current?.handle?.onVisible?.(!v);
}

// ---------------------------------------------------------------- 3D tilt + shine on cards and tiles

const TILT = '.card, .ctile, .vid, .tile, .mini';
let tilted = null;
function untilt(el) {
  if (!el) return;
  el.classList.remove('tilting');
  el.style.removeProperty('--rx');
  el.style.removeProperty('--ry');
}
document.addEventListener('pointermove', (e) => {
  if (reduceMotion() || e.pointerType === 'touch') return;
  const el = e.target.closest?.(TILT);
  if (el !== tilted) { untilt(tilted); tilted = el; }
  if (!el) return;
  if (!el.classList.contains('tiltable')) {
    el.classList.add('tiltable');
    el.append(h('span', { class: 'shine', 'aria-hidden': 'true' }));
  }
  const r = el.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  const y = (e.clientY - r.top) / r.height;
  const k = r.width > 260 ? 5 : 9; // big panels tilt less
  el.style.setProperty('--rx', `${((0.5 - y) * k).toFixed(2)}deg`);
  el.style.setProperty('--ry', `${((x - 0.5) * k).toFixed(2)}deg`);
  el.style.setProperty('--mx', `${(x * 100).toFixed(1)}%`);
  el.style.setProperty('--my', `${(y * 100).toFixed(1)}%`);
  el.classList.add('tilting');
}, { passive: true });
document.addEventListener('pointerleave', () => { untilt(tilted); tilted = null; });
window.addEventListener('resize', moveIndicator);

// pywebview tells us about minimize/restore; a normal browser fires visibilitychange
window.campi = { setHidden };
document.addEventListener('visibilitychange', () => setHidden(document.hidden));

function activeVideo() {
  return modalVideo() || detailVideo() || current?.handle?.video?.() || $('#main video');
}

document.addEventListener('keydown', (e) => {
  const t = e.target;
  const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement;
  if (e.key === 'Escape') {
    if (typing) { t.blur(); return; }
    if (!closeModal()) closeDetail();
    return;
  }
  if (typing || e.ctrlKey || e.altKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  if (k === 'j' || k === 'k') {
    const d = k === 'j' ? 1 : -1;
    if (detailOpen() && !modalVideo()) detailStep(d); else selection.move(d);
    e.preventDefault();
  } else if (k === 's') {
    if (detailOpen() && !modalVideo()) starDetail(); else selection.star();
    e.preventDefault();
  } else if (e.key === 'Enter') {
    if (t instanceof HTMLButtonElement || t instanceof HTMLAnchorElement) return;
    selection.open();
  } else if (e.key === '[' || e.key === ']') {
    const r = stepRate(activeVideo(), e.key === ']' ? 1 : -1);
    toast(`Playback speed ${r}×`);
    e.preventDefault();
  } else if (e.key === ' ') {
    if (t instanceof HTMLButtonElement) return; // native handling
    const v = activeVideo();
    if (v) {
      if (v.paused) v.play().catch(() => {}); else v.pause();
      e.preventDefault();
    }
  }
});

window.addEventListener('hashchange', route);
route();
