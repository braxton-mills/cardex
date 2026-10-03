// Shared helpers and components for the Campi UI (plain ES modules, no build step, no third-party code).

export const $ = (sel, root = document) => root.querySelector(sel);

// h('div', {class: 'x', onclick: fn, dataset: {id}}, child, [children], 'text')
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
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

export async function api(path, opts = {}) {
  const init = { ...opts, headers: { 'Content-Type': 'application/json' } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    const e = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    e.status = r.status;
    throw e;
  }
  return r.json();
}
export const post = (path, body) => api(path, { method: 'POST', body });

export function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== '' && v != null && v !== false) p.set(k, v === true ? '1' : v);
  const s = p.toString();
  return s ? `?${s}` : '';
}

// Per-viewer conveniences only (remembered filters); the page works without it.
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

// ---------------------------------------------------------------- formatting

const pad = (n) => String(n).padStart(2, '0');
export const fmt = {
  // *_local strings carry the PC's offset; show their wall-clock part as-is
  time: (iso) => (iso ? iso.slice(11, 19) : ''),
  hm: (iso) => (iso ? iso.slice(11, 16) : ''),
  date: (iso) => (iso ? iso.slice(0, 10) : ''),
  dateTime: (iso) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 19)}` : ''),
  day(isoDate) {
    const d = new Date(`${isoDate}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  },
  ago(epoch) {
    if (!epoch) return 'never';
    const s = Math.max(0, Date.now() / 1000 - epoch);
    if (s < 90) return `${Math.round(s)}s ago`;
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    if (s < 172800) return `${(s / 3600).toFixed(1)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  },
  pct: (p) => (p == null ? '' : `${Math.round(p * 100)}%`),
  size(b) {
    if (b == null) return '';
    if (b >= 1e9) return `${(b / 1e9).toFixed(2)} GB`;
    return `${(b / 1e6).toFixed(b >= 1e8 ? 0 : 1)} MB`;
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

// ---------------------------------------------------------------- components

export function starButton(starred, onToggle, title = 'Star (S)') {
  const b = h('button', { class: `star ${starred ? 'on' : ''}`, title, 'aria-pressed': String(!!starred) }, starred ? '★' : '☆');
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const next = !b.classList.contains('on');
    try {
      await onToggle(next);
      b.classList.toggle('on', next);
      b.textContent = next ? '★' : '☆';
      b.setAttribute('aria-pressed', String(next));
    } catch (err) { toast(`Couldn't save: ${err.message}`, 'error'); }
  });
  return b;
}

export function badge(text, cls = '') { return h('span', { class: `badge ${cls}` }, text); }

export function sightingCard(s, onOpen) {
  const card = h('article', { class: `card sighting ${s.hidden ? 'is-hidden' : ''}`, tabindex: '-1', dataset: { id: s.id } },
    h('div', { class: 'crop' }, s.crop_url ? h('img', { src: s.crop_url, loading: 'lazy', alt: s.label || 'vehicle' }) : h('div', { class: 'nocrop' }, 'no crop')),
    h('div', { class: 'meta' },
      h('div', { class: 'label', title: s.label || '' }, s.label || '(unlabelled)'),
      h('div', { class: 'sub' },
        h('span', {}, fmt.time(s.started_local)),
        s.direction ? h('span', { class: 'dir', title: s.direction === 'LR' ? 'left to right' : 'right to left' }, fmt.dir(s.direction)) : null,
        s.label_by === 'user' ? null : h('span', { class: 'conf' }, fmt.pct(s.confidence)),
        badge(fmt.labelBy(s.label_by), `by-${s.label_by}`)),
      (s.year_range || s.color) ? h('div', { class: 'sub2' }, [s.year_range, s.color].filter(Boolean).join(' · ')) : null,
      h('div', { class: 'flags' },
        s.unsure ? badge('unsure', 'warn') : null,
        s.stationary ? badge('parked', 'muted') : null,
        s.hidden ? badge('hidden', 'muted') : null)),
    starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { value: v }).then((r) => { s.starred = r.starred; })));
  card.addEventListener('click', () => onOpen(s, card));
  card._sighting = s;
  return card;
}

export function empty(title, text) {
  return h('div', { class: 'empty' }, h('h3', {}, title), text ? h('p', {}, text) : null);
}

export function sightingsOff(status) {
  return empty('Sightings is off',
    'Vehicle sightings are optional. Install them with install.ps1 -Sightings, set [sightings] enabled = true in '
    + 'config.toml, then run campi restart.' + (status?.sightings_db ? '' : ''));
}

// Sightings disabled in config.toml but a database exists: say so above the history.
export function offBanner(status) {
  return status && !status.sightings_enabled ? h('p', { class: 'banner' }, 'Sightings is off; showing what was recorded before.') : null;
}

export function revealButton(root, path) {
  return h('button', {
    class: 'ghost small', title: 'Show in folder',
    onclick: async (e) => {
      e.stopPropagation();
      try { await post('/api/desktop/reveal', { root, path }); } catch (err) { toast(err.status === 501 ? 'Show in folder only works on this PC' : err.message, 'error'); }
    },
  }, 'Show in folder');
}

// ---------------------------------------------------------------- modal player + "View in timelapse"

let modal = null;
export function closeModal() {
  if (!modal) return false;
  const v = modal.querySelector('video');
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
  modal.remove();
  modal = null;
  return true;
}
export const modalVideo = () => modal?.querySelector('video') || null;

export function openPlayer({ url, title, offset = 0, pause = false, note = null, reveal = null, autoplay = true }) {
  closeModal();
  const video = h('video', { src: url, controls: true, preload: 'auto', playsinline: true });
  video.addEventListener('loadedmetadata', () => {
    if (offset) video.currentTime = Math.min(offset, Math.max(0, video.duration - 0.05));
    if (pause) video.pause(); else if (autoplay) video.play().catch(() => {});
  }, { once: true });
  video.addEventListener('error', () => toast('This video is no longer available (it may have expired).', 'error'));
  modal = h('div', { class: 'modal', onclick: (e) => { if (e.target === modal) closeModal(); } },
    h('div', { class: 'modal-box' },
      h('header', {}, h('h3', {}, title || ''), note, h('div', { class: 'grow' }), reveal, h('button', { class: 'ghost', onclick: closeModal, title: 'Close (Esc)' }, '✕')),
      video));
  document.body.append(modal);
  return video;
}

export async function viewInTimelapse(ts, what = '') {
  let r;
  try {
    r = await api(`/api/seek${qs({ ts })}`);
  } catch (e) {
    toast(e.status === 404
      ? 'No video covers that time: 10-minute clips are kept 24 h, and the daily video is made after midnight.'
      : e.message, 'error');
    return;
  }
  const note = h('span', { class: 'seeknote' },
    `frame ${fmt.time(r.frame_local)} · `,
    r.approximate ? badge('approximate (daily video)', 'warn') : badge(r.kind === 'clip' ? '10-minute clip' : r.kind, 'ok'),
    ' · Space plays');
  openPlayer({ url: r.url, title: `${what ? `${what} · ` : ''}${r.name}`, offset: r.offset_s + 0.01, pause: true, note,
    reveal: revealButton(r.kind === 'clip' ? 'clips' : 'daily', r.name) });
}

// ---------------------------------------------------------------- sighting detail panel

const detail = { el: null, s: null, list: null };
export const detailOpen = () => !!detail.el;
export function closeDetail() {
  if (!detail.el) return false;
  detail.el.querySelectorAll('video').forEach((v) => { v.pause(); v.removeAttribute('src'); v.load(); });
  detail.el.remove();
  detail.el = null;
  detail.s = null;
  return true;
}
export const detailVideo = () => detail.el?.querySelector('video') || null;

// list: optional {ids: [...], onChange(s)} for J/K inside the panel and to refresh the card that opened it
export async function openSighting(id, list = null) {
  let s;
  try { s = await api(`/api/sightings/${encodeURIComponent(id)}`); } catch (e) { toast(e.message, 'error'); return; }
  closeDetail();
  detail.s = s;
  detail.list = list;
  const changed = (r) => { Object.assign(s, r); list?.onChange?.(r); };
  const labelInput = h('input', { type: 'text', value: s.label_by === 'user' ? s.label : '', placeholder: s.label || 'label', list: 'label-options' });
  const saveLabel = async (value) => {
    try {
      const r = await post(`/api/sightings/${s.id}/label`, { label: value });
      changed(r);
      toast(value ? `Label set to ${r.label}` : 'Correction removed');
      openSighting(s.id, list);
    } catch (e) { toast(e.message, 'error'); }
  };
  const runners = (s.runner_ups || []).map((r) => h('li', {}, h('span', {}, r.label), h('span', { class: 'muted' }, fmt.pct(r.p))));
  const origin = s.label_by === 'cloud'
    ? `Gemini (${fmt.pct(s.confidence)}); SigLIP said ${s.siglip_label || '?'} ${fmt.pct(s.siglip_confidence)}`
    : s.label_by === 'user'
      ? `You (model said ${s.model_label || '?'}${s.source === 'cloud' ? ', via Gemini' : ''})`
      : `SigLIP (${fmt.pct(s.confidence)})${s.cloud?.state === 'pending' ? '; Gemini answer pending' : ''}`;
  const rows = [
    ['When', fmt.dateTime(s.started_local) + (s.ended_local && s.ended_local !== s.started_local ? `–${fmt.time(s.ended_local)}` : '')],
    ['Class', s.yolo_class], ['Direction', s.direction ? `${fmt.dir(s.direction)} ${s.direction === 'LR' ? 'left to right' : 'right to left'}` : (s.stationary ? 'stationary' : '')],
    ['Years', s.year_range], ['Color', s.color], ['Label by', origin],
    ['Flags', [s.unsure && 'unsure', s.stationary && 'parked', s.hidden && 'hidden'].filter(Boolean).join(', ')],
  ].filter(([, v]) => v);
  detail.el = h('aside', { class: 'detail' },
    h('header', {},
      h('div', {}, h('h2', {}, s.label || '(unlabelled)'), h('div', { class: 'muted' }, [s.make, s.model].filter(Boolean).join(' · '))),
      h('div', { class: 'grow' }),
      starButton(s.starred, (v) => post(`/api/sightings/${s.id}/star`, { value: v }).then(changed)),
      h('button', { class: 'ghost', onclick: closeDetail, title: 'Close (Esc)' }, '✕')),
    h('div', { class: 'detail-body' },
      h('div', { class: 'media-row' },
        s.crop_url ? h('a', { href: s.crop_url, target: '_blank', class: 'crop-big' }, h('img', { src: s.crop_url, alt: 'crop' })) : null,
        h('dl', {}, rows.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]))),
      h('div', { class: 'actions' },
        h('button', { class: 'primary', onclick: () => viewInTimelapse(s.seek_ts, s.label) }, 'View in timelapse'),
        h('button', {
          onclick: async () => {
            try { const r = await post(`/api/sightings/${s.id}/hide`, { value: !s.hidden }); changed(r); toast(r.hidden ? 'Hidden from lists and counts' : 'Unhidden'); openSighting(s.id, list); } catch (e) { toast(e.message, 'error'); }
          },
        }, s.hidden ? 'Unhide' : 'Hide'),
        revealButton('sightings', (s.crop_url || '').replace('/media/sightings/', '').split('/').map(decodeURIComponent).join('/'))),
      h('form', { class: 'relabel', onsubmit: (e) => { e.preventDefault(); const v = labelInput.value.trim(); if (v) saveLabel(v); } },
        h('label', {}, 'Correct label'), labelInput, h('button', { type: 'submit' }, 'Save'),
        s.label_by === 'user' ? h('button', { type: 'button', class: 'ghost', onclick: () => saveLabel(null) }, 'Undo correction') : null),
      runners.length ? h('section', {}, h('h4', {}, s.label_by === 'cloud' ? "SigLIP's runner-ups" : 'Runner-up guesses'), h('ol', { class: 'runners' }, runners)) : null,
      h('section', {}, h('h4', {}, 'Clip'),
        s.clip_url ? h('video', { src: s.clip_url, controls: true, loop: true, muted: true, autoplay: true, playsinline: true, preload: 'auto' })
          : h('p', { class: 'muted' }, 'No clip (sighting clips are kept for a limited number of days).')),
      s.frame_url ? h('section', {}, h('h4', {}, 'Full frame'), h('a', { href: s.frame_url, target: '_blank' }, h('img', { class: 'frame', src: s.frame_url, alt: 'full frame', loading: 'lazy' }))) : null));
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
  if (!detail.s) return;
  const b = detail.el.querySelector('header .star');
  b?.click();
}

let labelOptionsLoaded = false;
export async function ensureLabelOptions(force = false) {
  if (labelOptionsLoaded && !force) return;
  labelOptionsLoaded = true;
  try {
    const c = await api('/api/collection');
    const dl = $('#label-options');
    fill(dl, ...c.items.map((i) => h('option', { value: i.label })));
  } catch { labelOptionsLoaded = false; }
}

// ---------------------------------------------------------------- status strip

export function statusStrip(st) {
  const svc = st.alive;
  const cap = st.capture || {};
  const clip = st.last_clip || {};
  const s = st.sightings || {};
  const pill = (ok, text, title) => h('span', { class: `pill ${ok === true ? 'ok' : ok === false ? 'bad' : 'warn'}`, title }, text);
  const items = [
    pill(svc, svc ? 'Service running' : 'Service stopped', svc ? `pid ${st.service.supervisor_pid}` : 'campi start'),
    pill(!!cap.connected, cap.connected ? `Stream ${cap.host || ''}` : 'Stream disconnected', cap.last_error || ''),
    h('span', { class: 'kv' }, 'last frame ', h('b', {}, fmt.ago(cap.last_frame_ts))),
    clip.status ? h('span', { class: 'kv', title: clip.out || clip.reason || clip.error || '' }, 'last clip ', h('b', {}, `${clip.status} ${fmt.ago(clip.finished)}`)) : null,
    svc && st.service.next_clip ? h('span', { class: 'kv' }, 'next ', h('b', {}, String(st.service.next_clip).slice(11, 16))) : null,
  ];
  if (st.sightings_enabled) {
    const dev = s.cpu_fallback ? 'CPU FALLBACK' : (s.device_name || s.device || '');
    const ok = s.state === 'running' && !s.cpu_fallback ? true : (s.state === 'crash-looping' || s.state === 'not installed' ? false : null);
    items.push(pill(ok, `Sightings ${s.state}${s.backend ? ` · ${s.backend}` : ''}${dev ? ` on ${dev}` : ''}`, s.line));
    if (s.cloud) items.push(h('span', { class: 'kv' }, 'Gemini ', h('b', {}, `${s.cloud.today ?? '?'}/${s.cloud.cap}`), s.cloud.pending ? ` (${s.cloud.pending} queued)` : ''));
  } else {
    items.push(pill(null, 'Sightings off'));
  }
  if (st.gaming) items.push(pill(st.gaming.active ? null : true, st.gaming.active ? `Gaming: ${st.gaming.exe}` : 'No game', st.gaming.line));
  if (st.render_queue && (st.render_queue.windows || st.render_queue.deferred)) {
    items.push(pill(null, `Render queue ${st.render_queue.windows}${st.render_queue.deferred ? ' · deferred' : ''}`,
      st.render_queue.oldest ? `oldest ending ${st.render_queue.oldest}` : ''));
  }
  items.push(h('span', { class: 'kv' }, 'disk ', h('b', {}, `${st.disk.free_gb.toFixed(0)} GB free`)));
  return h('div', { class: 'status-strip' }, items);
}
