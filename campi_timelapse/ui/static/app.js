// Router, keyboard and visibility handling. Each view module exports mount(el, params) -> {unmount, onVisible?}.
import {
  $, closeDetail, closeModal, detailOpen, detailStep, detailVideo, modalVideo, selection, starDetail,
} from './lib.js';
import * as today from './views/today.js';
import * as highlights from './views/highlights.js';
import * as sightings from './views/sightings.js';
import * as collection from './views/collection.js';
import * as timelapse from './views/timelapse.js';
import * as live from './views/live.js';

const VIEWS = { today, highlights, sightings, collection, timelapse, live };
let current = null; // {name, handle}
let hidden = document.hidden;

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
  const main = $('#main');
  main.replaceChildren();
  main.scrollTop = 0;
  current = { name, handle: null };
  const mine = current;
  const handle = await VIEWS[name].mount(main, params, { isHidden: () => hidden });
  if (current === mine) current.handle = handle; else handle?.unmount?.(); // navigated away while mounting
}

function setHidden(h) {
  if (h === hidden) return;
  hidden = h;
  current?.handle?.onVisible?.(!h);
}

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
  } else if (e.key === ' ') {
    if (t instanceof HTMLVideoElement || t instanceof HTMLButtonElement) return; // native handling
    const v = activeVideo();
    if (v) {
      if (v.paused) v.play().catch(() => {}); else v.pause();
      e.preventDefault();
    }
  }
});

window.addEventListener('hashchange', route);
route();
