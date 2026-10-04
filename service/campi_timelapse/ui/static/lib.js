// Shared helpers and components for the Campi UI (plain ES modules, no build step, no third-party code).
import { icon } from './icons.js';

export { icon };
export const $ = (sel, root = document) => root.querySelector(sel);

// h('div', {class: 'x', onclick: fn, dataset: {id}}, child, [children], 'text')
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') for (const [sk, sv] of Object.entries(v)) { if (sk.startsWith('--')) el.style.setProperty(sk, sv); else el.style[sk] = sv; }
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (c) => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  };
  children.forEach(add);
  return el;
}

// SVG element (namespaced) with attributes
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  children.flat().forEach((c) => c && el.append(c));
  return el;
}

// el.replaceChildren() that skips null/false (replaceChildren would insert the text "null")
export function fill(el, ...children) {
  el.replaceChildren();
  const add = (c) => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(add); else el.append(c);
  };
  children.forEach(add);
  return el;
}

// The desktop is a paired device like any phone: `campi ui` opens /#token=<t> (a fragment never reaches the
// server); it's kept for this window only and taken off the address.
const TOKEN_KEY = 'campi.token';
function takeToken() {
  const m = location.hash.match(/^#token=([A-Za-z0-9_-]+)/);
  if (m) {
    try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch { /* storage blocked: this page load only */ }
    window.campiToken = m[1];
    history.replaceState(null, '', `${location.pathname}#/today`);
  }
}
takeToken();
const token = () => { try { return sessionStorage.getItem(TOKEN_KEY) || window.campiToken; } catch { return window.campiToken; } };

function signedOut() {
  if ($('#signed-out')) return;
  document.body.append(h('div', { id: 'signed-out', class: 'modal' }, h('div', { class: 'modal-box signed-out' },
    h('h2', {}, 'Open Campi from the Start menu'),
    h('p', { class: 'muted' }, 'This window has no valid access token (it was opened directly, or the desktop device was '
      + 'revoked). Close it and run campi ui again.'))));
}

export async function api(path, opts = {}) {
  const init = { ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    let code = '';
    try { const b = await r.json(); msg = b.error?.message || msg; code = b.error?.code || ''; } catch { /* not JSON */ }
    if (r.status === 401) signedOut();
    const e = new Error(msg);
    e.status = r.status;
    e.code = code;
    throw e;
  }
  return r.status === 204 ? null : r.json();
}

// epoch seconds of a contract datetime ("2026-10-03T14:05:12.345-05:00")
export const epoch = (iso) => (iso ? Date.parse(iso) / 1000 : null);
// where "View in timelapse" lands for a sighting: the middle of the pass
export const seekTs = (s) => (epoch(s.started_at) + epoch(s.ended_at || s.started_at)) / 2;
// file names for "Show in folder"
export const clipFile = (id) => `campi_${id}.mp4`;
export const dailyFile = (day) => `campi_daily_${day}.mp4`;
export const archiveFile = (part) => `campi_archive_${String(part).padStart(3, '0')}.mp4`;
// a /media/sightings/... URL (signed) -> its path relative to the sightings folder
export const sightingRel = (url) => (url || '').split('?')[0].replace('/media/sightings/', '').split('/').map(decodeURIComponent).join('/');
export const post = (path, body) => api(path, { method: 'POST', body });

export function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== '' && v != null && v !== false) p.set(k, v === true ? '1' : v);
  const s = p.toString();
  return s ? `?${s}` : '';
}

// Per-viewer conveniences only (remembered filters, collapsed nav); the page works without it.
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem(`campi.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`campi.${k}`, JSON.stringify(v)); } catch { /* storage blocked */ } },
};

let toastTimer;
export function toast(msg, kind = 'info') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, 4000);
}

// ---------------------------------------------------------------- motion helpers (all skipped with reduced motion)

export const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const PARTY = ['#ff7a59', '#ff4d8d', '#c04cfd', '#8b5cf6', '#22d3ee', '#ffcf5a', '#34d399'];

// Number that counts up from the last value shown under the same key (0 the first time).
const lastValues = new Map();
export function counter(key, value, { decimals = 0, ms = 900 } = {}) {
  const el = h('span', { class: 'num' });
  const show = (v) => { el.textContent = Number(v).toFixed(decimals); };
  if (!Number.isFinite(value)) { el.textContent = String(value); return el; }
  const from = lastValues.has(key) ? lastValues.get(key) : 0;
  lastValues.set(key, value);
  if (from === value || reduceMotion()) { show(value); return el; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / ms);
    show(from + (value - from) * (1 - (1 - p) ** 3));
    if (p < 1) requestAnimationFrame(step);
  };
  show(from);
  requestAnimationFrame(step);
  return el;
}

// Staggered pop-in for a list of freshly added nodes.
export function stagger(nodes, start = 0) {
  nodes.forEach((n, i) => { n.style.setProperty('--i', String(Math.min(start + i, 24))); n.classList.add('pop-in'); });
  return nodes;
}

// Particles flying out of an element (stars).
export function burst(target, n = 12) {
  if (reduceMotion() || !target?.isConnected) return;
  const r = target.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + Math.random() * 0.5;
    const d = 26 + Math.random() * 26;
    const p = h('i', { class: 'particle' });
    p.style.cssText = `left:${cx}px;top:${cy}px;--dx:${Math.cos(a) * d}px;--dy:${Math.sin(a) * d}px;--c:${PARTY[i % PARTY.length]}`;
    p.addEventListener('animationend', () => p.remove());
    document.body.append(p);
  }
}

// Confetti shower (new catches).
export function confetti(n = 90) {
  if (reduceMotion()) return;
  const box = h('div', { class: 'confetti' });
  for (let i = 0; i < n; i++) {
    const c = h('i');
    c.style.cssText = `left:${Math.random() * 100}%;--c:${PARTY[i % PARTY.length]};--dx:${(Math.random() - 0.5) * 260}px;`
      + `--r:${(Math.random() - 0.5) * 1440}deg;--t:${1.8 + Math.random() * 1.6}s;--d:${Math.random() * 0.5}s;`
      + `width:${6 + Math.random() * 6}px;height:${8 + Math.random() * 10}px`;
    box.append(c);
  }
  document.body.append(box);
  setTimeout(() => box.remove(), 4200);
}

// ---------------------------------------------------------------- formatting

const pad = (n) => String(n).padStart(2, '0');
export const fmt = {
  // API datetimes carry the PC's offset; show their wall-clock part as-is
  time: (iso) => (iso ? iso.slice(11, 19) : ''),
  hm: (iso) => (iso ? iso.slice(11, 16) : ''),
  date: (iso) => (iso ? iso.slice(0, 10) : ''),
  dateTime: (iso) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 19)}` : ''),
  day(isoDate) {
    const d = new Date(`${isoDate}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  },
  weekday: (isoDate) => new Date(`${isoDate}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short' }),
  ago(epoch) {
    if (!epoch) return 'never';
    const s = Math.max(0, Date.now() / 1000 - epoch);
    if (s < 90) return `${Math.round(s)}s ago`;
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    if (s < 172800) return `${(s / 3600).toFixed(1)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  },
  // compact age for big numerals: [value, unit]
  agoParts(epoch) {
    if (!epoch) return ['–', ''];
    const s = Math.max(0, Date.now() / 1000 - epoch);
    if (s < 90) return [String(Math.round(s)), 's'];
    if (s < 5400) return [String(Math.round(s / 60)), 'min'];
    if (s < 172800) return [(s / 3600).toFixed(1), 'h'];
    return [String(Math.round(s / 86400)), 'd'];
  },
  pct: (p) => (p == null ? '' : `${Math.round(p * 100)}%`),
  size(b) {
    if (b == null) return '';
    if (b >= 1e9) return `${(b / 1e9).toFixed(2)} GB`;
    return `${(b / 1e6).toFixed(b >= 1e8 ? 0 : 1)} MB`;
  },
  clock(t) {
    if (!Number.isFinite(t)) return '0:00';
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    return `${m}:${s < 10 ? '0' : ''}${s.toFixed(t < 60 ? 1 : 0)}`;
  },
  labelBy: (s) => ({ siglip: 'SigLIP', cloud: 'Gemini', user: 'You' }[s] || s || ''),
  dir: (d) => ({ LR: '→', RL: '←' }[d] || ''),
  localDate: (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
};

// ---------------------------------------------------------------- selection (J/K/S/Enter)

export const selection = {
  items: [],
  i: -1,
  handlers: {},
  set(items, handlers = {}) {
    this.items = items;
    this.handlers = handlers;
    this.i = Math.min(this.i, items.length - 1);
    if (this.i >= 0) this.mark();
  },
  clear() { this.items = []; this.i = -1; this.handlers = {}; },
  current() { return this.items[this.i]; },
  move(d) {
    if (!this.items.length) return;
    this.i = Math.max(0, Math.min(this.items.length - 1, this.i + d));
    this.mark();
    this.handlers.onMove?.(this.current());
  },
  select(node) { this.i = this.items.indexOf(node); this.mark(); },
  mark() {
    this.items.forEach((n, k) => n.classList.toggle('sel', k === this.i));
    this.current()?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  },
  open() { const n = this.current(); if (n) this.handlers.open?.(n); },
  star() { const n = this.current(); if (n) this.handlers.star?.(n); },
};

// ---------------------------------------------------------------- small components

export function iconButton(name, title, onclick, cls = '') {
  return h('button', {
    class: `icon-btn ${cls}`, title, 'aria-label': title,
    onclick: onclick && ((e) => { e.stopPropagation(); onclick(e); }),
    disabled: !onclick,
  }, icon(name, 18));
}

// Pill tabs (single or multi select): items [[key, label]], isOn(key), onPick(key)
export function tabs(items, isOn, onPick) {
  return h('div', { class: 'tabs' }, items.map(([k, label]) => h('button', {
    class: `tab ${isOn(k) ? 'on' : ''}`, onclick: () => onPick(k),
  }, label)));
}

export function starButton(starred, onToggle, title = 'Star (S)') {
  const b = h('button', { class: `star ${starred ? 'on' : ''}`, title, 'aria-label': title, 'aria-pressed': String(!!starred) }, icon('star', 18));
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const next = !b.classList.contains('on');
    try {
      await onToggle(next);
      b.classList.toggle('on', next);
      b.setAttribute('aria-pressed', String(next));
      b.classList.remove('pop');
      void b.offsetWidth; // restart the animation
      b.classList.add('pop');
      if (next) burst(b);
    } catch (err) { toast(`Couldn't save: ${err.message}`, 'error'); }
  });
  return b;
}

export function badge(text, cls = '') { return h('span', { class: `badge ${cls}` }, text); }

// Card header: round icon chip, title, optional right side
export function cardHead(iconName, title, ...right) {
  return h('header', { class: 'card-head' },
    iconName ? h('span', { class: 'head-ic' }, icon(iconName, 18)) : null,
    h('h2', {}, title), h('div', { class: 'grow' }), right);
}

export function viewHead(...children) {
  return h('div', { class: 'view-head' }, children);
}

export function sightingCard(s, onOpen) {
  const card = h('article', { class: `card sighting ${s.hidden ? 'is-hidden' : ''}`, tabindex: '-1', dataset: { id: s.id } },
    h('div', { class: 'crop' },
      s.media.crop ? h('img', { src: s.media.crop, loading: 'lazy', alt: s.label || 'vehicle' }) : h('div', { class: 'nocrop' }, 'no crop'),
      h('div', { class: 'crop-flags' },
        s.unsure ? badge('unsure', 'warn') : null,
        s.stationary ? badge('parked', 'muted') : null,
        s.hidden ? badge('hidden', 'muted') : null),
      starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { starred: v }).then((r) => { s.starred = r.starred; }))),
    h('div', { class: 'meta' },
      h('div', { class: 'label', title: s.label || '' }, s.label || '(unlabelled)'),
      h('div', { class: 'sub' },
        h('span', { class: 'num' }, fmt.time(s.started_at)),
        s.direction ? h('span', { class: 'dir', title: s.direction === 'LR' ? 'left to right' : 'right to left' }, fmt.dir(s.direction)) : null,
        s.decided_by === 'user' ? null : h('span', { class: 'num' }, fmt.pct(s.confidence)),
        h('span', { class: 'grow' }),
        s.cloud_status === 'pending' ? badge('asking Gemini', 'by-cloud') : badge(fmt.labelBy(s.decided_by), `by-${s.decided_by}`)),
      (s.year_range || s.color) ? h('div', { class: 'sub2' }, [s.year_range, s.color].filter(Boolean).join(' · ')) : null));
  card.addEventListener('click', () => onOpen(s, card));
  card._sighting = s;
  return card;
}

export function empty(title, text) {
  return h('div', { class: 'empty' }, h('h3', {}, title), text ? h('p', {}, text) : null);
}

export function sightingsOff() {
  return empty('Sightings is off',
    'Vehicle sightings are optional. Install them with install.ps1 -Sightings, set [sightings] enabled = true in '
    + 'config.toml, then run campi restart.');
}

// Sightings disabled in config.toml but a database exists: say so above the history.
export function offBanner(status) {
  return status && !status.sightings.enabled ? h('p', { class: 'banner' }, 'Sightings is off; showing what was recorded before.') : null;
}
// Sightings off and never recorded: the sightings views show sightingsOff() instead
export const noSightings = (status) => !!status && !status.sightings.enabled && !status.sightings.has_history;

// Explorer opens in this desktop session: through the Campi window (pywebview) when there is one, since the API
// may run under the background service; a server started by `campi ui --browser` does it itself.
export function revealButton(root, path, withLabel = false) {
  const go = async () => {
    try {
      const shell = window.pywebview?.api;
      if (shell?.reveal) {
        const r = await shell.reveal(root, path);
        if (r && r.error) toast(r.error, 'error');
      } else {
        await post('/api/desktop/reveal', { root, path });
      }
    } catch (err) { toast(err.message, 'error'); }
  };
  if (!withLabel) return iconButton('folder', 'Show in folder', go, 'small');
  return h('button', { class: 'pill-btn', onclick: (e) => { e.stopPropagation(); go(); } }, icon('folder', 16), 'Show in folder');
}

// Collection ring: the label list as a circle, one colored arc per rarity tier for what's been caught, the
// empty track for what hasn't. segments: [{key, value}] in drawing order; total = every label on the list.
export const TIER_ORDER = ['rare', 'uncommon', 'common'];
export function ring(segments, total, center, { animate = true } = {}) {
  const r = 92;
  const C = 2 * Math.PI * r;
  const gap = segments.filter((g) => g.value > 0).length > 1 ? 6 : 0; // px between arcs
  let at = 0;
  const arcs = segments.filter((g) => g.value > 0).map((g, i) => {
    const len = Math.max(0, (g.value / Math.max(1, total)) * C - gap);
    const arc = svg('circle', {
      cx: 120, cy: 120, r, class: `ring-seg ${g.key}`, 'stroke-dasharray': `${len} ${C}`, 'stroke-dashoffset': String(-at),
    });
    arc.style.setProperty('--len', String(len));
    arc.style.setProperty('--C', String(C));
    arc.style.setProperty('--d', `${i * 140}ms`);
    at += len + gap;
    return arc;
  });
  return h('div', { class: `ring ${animate ? 'anim' : ''}` },
    svg('svg', { viewBox: '0 0 240 240' },
      svg('circle', { cx: 120, cy: 120, r, class: 'ring-track' }),
      svg('g', { transform: 'rotate(-90 120 120)' }, arcs)),
    h('div', { class: 'ring-center' }, center));
}

// The ring is about the label list (sightings_labels.txt); discovered and retired labels are counted beside it.
export function listStats(c) {
  const list = c.items.filter((i) => i.origin === 'labels_file');
  return { list, total: list.length, caught: list.filter((i) => i.count > 0).length,
    discovered: c.items.filter((i) => i.origin === 'discovered' && i.count > 0).length };
}

// caught list labels per tier, for ring()
export function tierSegments(items) {
  return TIER_ORDER.map((key) => ({ key, value: items.filter((i) => i.tier === key && i.origin === 'labels_file').length }));
}

// ---------------------------------------------------------------- video player (amber controls)

// Wraps a <video> with the app's controls: scrubber with knob, times, prev / play / next, mute, full screen.
export function player(video, { onPrev = null, onNext = null, compact = false } = {}) {
  video.controls = false;
  video.playsInline = true;
  const fillBar = h('div', { class: 'scrub-fill' });
  const knob = h('div', { class: 'scrub-knob' });
  const bar = h('div', { class: 'scrub', title: 'Seek' }, h('div', { class: 'scrub-track' }, fillBar), knob);
  const cur = h('span', { class: 't num' }, '0:00');
  const dur = h('span', { class: 't num' }, '0:00');
  const playBtn = h('button', { class: 'play-btn', title: 'Play / pause (Space)', 'aria-label': 'Play / pause' }, icon('play', 22));
  const muteBtn = iconButton(video.muted ? 'mute' : 'volume', 'Mute', () => { video.muted = !video.muted; });
  const wrap = h('div', { class: `player ${compact ? 'compact' : ''}` }, video,
    h('div', { class: 'controls' },
      h('div', { class: 'scrub-row' }, bar),
      h('div', { class: 'ctl-row' }, cur, h('div', { class: 'grow' }),
        onPrev ? iconButton('prev', 'Previous', onPrev, 'ghost') : null,
        playBtn,
        onNext ? iconButton('next', 'Next', onNext, 'ghost') : null,
        h('div', { class: 'grow' }), muteBtn,
        iconButton('maximize', 'Full screen', () => (document.fullscreenElement ? document.exitFullscreen() : wrap.requestFullscreen?.()), 'ghost'),
        dur)));
  const toggle = () => (video.paused ? video.play().catch(() => {}) : video.pause());
  playBtn.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
  video.addEventListener('click', toggle);
  video.addEventListener('dblclick', () => (document.fullscreenElement ? document.exitFullscreen() : wrap.requestFullscreen?.()));
  const paint = () => {
    const d = video.duration;
    const p = Number.isFinite(d) && d > 0 ? Math.min(1, video.currentTime / d) : 0;
    fillBar.style.width = `${p * 100}%`;
    knob.style.left = `${p * 100}%`;
    cur.textContent = fmt.clock(video.currentTime);
    dur.textContent = fmt.clock(d);
  };
  const state = () => {
    fill(playBtn, icon(video.paused ? 'play' : 'pause', 22));
    wrap.classList.toggle('playing', !video.paused);
  };
  const vol = () => fill(muteBtn, icon(video.muted ? 'mute' : 'volume', 18));
  ['timeupdate', 'durationchange', 'seeked', 'loadedmetadata'].forEach((ev) => video.addEventListener(ev, paint));
  ['play', 'pause', 'ended'].forEach((ev) => video.addEventListener(ev, state));
  video.addEventListener('volumechange', vol);
  // drag to seek
  const seekTo = (x) => {
    const r = bar.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (x - r.left) / r.width));
    if (Number.isFinite(video.duration)) video.currentTime = p * video.duration;
    paint();
  };
  bar.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    bar.setPointerCapture(e.pointerId);
    bar.classList.add('drag');
    seekTo(e.clientX);
    const move = (m) => seekTo(m.clientX);
    const up = () => { bar.classList.remove('drag'); bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', up);
  });
  state();
  paint();
  return wrap;
}

export function releaseVideo(v) {
  if (!v) return;
  v.pause();
  v.removeAttribute('src');
  v.load();
}

// ---------------------------------------------------------------- modal player + "View in timelapse"

let modal = null;
export function closeModal() {
  if (!modal) return false;
  releaseVideo(modal.querySelector('video'));
  modal.remove();
  modal = null;
  return true;
}
export const modalVideo = () => modal?.querySelector('video') || null;

export function openPlayer({ url, title, offset = 0, pause = false, note = null, reveal = null, autoplay = true }) {
  closeModal();
  const video = h('video', { src: url, preload: 'auto' });
  video.addEventListener('loadedmetadata', () => {
    if (offset) video.currentTime = Math.min(offset, Math.max(0, video.duration - 0.05));
    if (pause) video.pause(); else if (autoplay) video.play().catch(() => {});
  }, { once: true });
  video.addEventListener('error', () => toast('This video is no longer available (it may have expired).', 'error'));
  modal = h('div', { class: 'modal', onclick: (e) => { if (e.target === modal) closeModal(); } },
    h('div', { class: 'modal-box' },
      h('header', { class: 'card-head' }, h('span', { class: 'head-ic' }, icon('film', 18)), h('h2', {}, title || ''), note,
        h('div', { class: 'grow' }), reveal, iconButton('x', 'Close (Esc)', closeModal)),
      player(video)));
  document.body.append(modal);
  return video;
}

const SEEK_WHY = {
  pending_render: 'That window hasn\'t been rendered yet; try again in a few minutes.',
  not_rendered: 'No video covers that time (that 10-minute window was skipped).',
  no_frames: 'Nothing was recorded around then (night, or the camera was offline).',
  expired: 'No video covers that time anymore: 10-minute clips are kept 24 h, and that day has no daily video.',
};

export async function viewInTimelapse(ts, what = '') {
  let r;
  try {
    r = await api(`/api/seek${qs({ ts: ts.toFixed(3) })}`);
  } catch (e) { toast(e.message, 'error'); return; }
  if (r.target === 'none') { toast(SEEK_WHY[r.reason] || 'No video covers that time.', 'error'); return; }
  const note = h('span', { class: 'seeknote' },
    h('span', { class: 'num' }, `around ${fmt.time(r.ts)}`),
    r.approximate ? badge('approximate · daily video', 'warn') : badge(r.target === 'clip' ? '10-minute clip' : 'daily video', 'amber'),
    h('span', { class: 'muted' }, 'Space plays'));
  openPlayer({ url: r.video, title: what || (r.clip_id || r.day), offset: r.offset_s + 0.01, pause: true, note,
    reveal: r.target === 'clip' ? revealButton('clips', clipFile(r.clip_id)) : revealButton('daily', dailyFile(r.day)) });
}

// ---------------------------------------------------------------- sighting detail panel

const detail = { el: null, s: null, list: null };
export const detailOpen = () => !!detail.el;
export function closeDetail() {
  if (!detail.el) return false;
  detail.el.querySelectorAll('video').forEach(releaseVideo);
  detail.el.remove();
  detail.el = null;
  detail.s = null;
  return true;
}
export const detailVideo = () => detail.el?.querySelector('video') || null;

// list: optional {ids: [...], onChange(s), onStep(id)} for J/K inside the panel and to refresh the card that opened it
export async function openSighting(id, list = null) {
  let s;
  try { s = await api(`/api/sightings/${encodeURIComponent(id)}`); } catch (e) { toast(e.message, 'error'); return; }
  closeDetail();
  detail.s = s;
  detail.list = list;
  const changed = (r) => { Object.assign(s, r); list?.onChange?.(r); };
  const labelInput = h('input', { type: 'text', value: s.decided_by === 'user' ? s.label : '', placeholder: s.label || 'label', list: 'label-options' });
  const saveLabel = async (value) => {
    try {
      const r = await post(`/api/sightings/${s.id}/label`, { label: value });
      changed(r);
      toast(value ? `Label set to ${r.label}` : 'Correction removed');
      openSighting(s.id, list);
    } catch (e) {
      toast(e.code === 'unknown_label' ? `"${value}" isn't in the collection; pick one from the list` : e.message, 'error');
    }
  };
  const CLOUD_NOTE = { pending: ' · asking Gemini…', capped: ' · Gemini daily cap reached', failed: ' · Gemini failed' };
  const origin = s.decided_by === 'cloud'
    ? `Gemini ${fmt.pct(s.confidence)} · SigLIP said ${s.siglip.label || '?'} ${fmt.pct(s.siglip.confidence)}`
    : s.decided_by === 'user'
      ? `You · model said ${s.machine.label || '?'}${s.machine.source === 'cloud' ? ' (Gemini)' : ''}`
      : `SigLIP ${fmt.pct(s.confidence)}${CLOUD_NOTE[s.cloud_status] || ''}`;
  const tiles = [
    ['When', `${fmt.time(s.started_at)}${s.ended_at && fmt.time(s.ended_at) !== fmt.time(s.started_at) ? `–${fmt.time(s.ended_at)}` : ''}`, fmt.day(s.day)],
    ['Class', s.yolo_class || '–', s.stationary ? 'parked' : (s.direction ? `${fmt.dir(s.direction)} ${s.direction === 'LR' ? 'left to right' : 'right to left'}` : '')],
    ['Years', s.year_range || '–', s.color || ''],
    ['Label by', fmt.labelBy(s.decided_by), origin],
  ];
  const runners = (s.runner_ups || []).map((r) => h('li', {},
    h('span', { class: 'r-label' }, r.label), h('span', { class: 'r-bar' }, h('span', { style: { width: `${Math.max(2, r.p * 100)}%` } })),
    h('span', { class: 'r-p num' }, fmt.pct(r.p))));
  const clip = s.media.clip ? h('video', { src: s.media.clip, loop: true, muted: true, autoplay: true, preload: 'auto' }) : null;
  detail.el = h('aside', { class: 'detail' },
    h('header', { class: 'detail-head' },
      h('div', { class: 'grow' },
        h('div', { class: 'eyebrow' }, [s.make, s.yolo_class].filter(Boolean).join(' · ') || 'sighting'),
        h('h2', {}, s.label || '(unlabelled)')),
      starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { starred: v }).then(changed)),
      iconButton('x', 'Close (Esc)', closeDetail)),
    h('div', { class: 'detail-body' },
      s.media.crop ? h('a', { href: s.media.crop, target: '_blank', class: 'crop-big' }, h('img', { src: s.media.crop, alt: 'crop' })) : null,
      h('div', { class: 'actions' },
        h('button', { class: 'primary', onclick: () => viewInTimelapse(seekTs(s), s.label) }, icon('play', 18), 'View in timelapse'),
        h('button', {
          class: 'pill-btn',
          onclick: async () => {
            try { const r = await post(`/api/sightings/${s.id}/hide`, { hidden: !s.hidden }); changed(r); toast(r.hidden ? 'Hidden from lists and counts' : 'Unhidden'); openSighting(s.id, list); } catch (e) { toast(e.message, 'error'); }
          },
        }, icon(s.hidden ? 'eye' : 'eyeOff', 16), s.hidden ? 'Unhide' : 'Hide'),
        s.media.crop ? revealButton('sightings', sightingRel(s.media.crop), true) : null),
      h('div', { class: 'mini-tiles' }, tiles.map(([k, v, sub]) => h('div', { class: 'mini' }, h('span', { class: 'cap' }, k), h('b', {}, v), sub ? h('span', { class: 'cap2' }, sub) : null))),
      [s.unsure && 'unsure', s.hidden && 'hidden'].filter(Boolean).length
        ? h('div', { class: 'flags' }, s.unsure ? badge('unsure', 'warn') : null, s.hidden ? badge('hidden', 'muted') : null) : null,
      h('form', { class: 'relabel', onsubmit: (e) => { e.preventDefault(); const v = labelInput.value.trim(); if (v) saveLabel(v); } },
        h('span', { class: 'head-ic' }, icon('tag', 16)), labelInput, h('button', { type: 'submit', class: 'pill-btn' }, 'Correct label'),
        s.decided_by === 'user' ? h('button', { type: 'button', class: 'pill-btn ghost', onclick: () => saveLabel(null) }, 'Undo') : null),
      runners.length ? h('section', {}, h('h4', {}, s.decided_by === 'cloud' ? "SigLIP's runner-ups" : 'Runner-up guesses'), h('ol', { class: 'runners' }, runners)) : null,
      h('section', {}, h('h4', {}, 'Clip'),
        clip ? player(clip, { compact: true }) : h('p', { class: 'muted' }, 'No clip (sighting clips are kept for a limited number of days).')),
      s.media.frame ? h('section', {}, h('h4', {}, 'Full frame'), h('a', { href: s.media.frame, target: '_blank' }, h('img', { class: 'frame', src: s.media.frame, alt: 'full frame', loading: 'lazy' }))) : null));
  document.body.append(detail.el);
  ensureLabelOptions();
}

export function detailStep(d) {
  const l = detail.list;
  if (!l?.ids?.length || !detail.s) return;
  const i = l.ids.indexOf(detail.s.id);
  const j = i + d;
  if (j < 0 || j >= l.ids.length) return;
  l.onStep?.(l.ids[j]);
  openSighting(l.ids[j], l);
}
export async function starDetail() {
  detail.el?.querySelector('.detail-head .star')?.click();
}

let labelOptionsLoaded = false;
export async function ensureLabelOptions(force = false) {
  if (labelOptionsLoaded && !force) return;
  labelOptionsLoaded = true;
  try {
    const c = await api('/api/collection');
    fill($('#label-options'), ...c.items.map((i) => h('option', { value: i.label })));
  } catch { labelOptionsLoaded = false; }
}
