import { api, fill, h } from '../lib.js';

// Every viewer is another full stream over the Pi's Wi-Fi, so the <img> only exists while this view is showing and
// the window is visible; switching views, minimizing or hiding the tab drops it (the server closes the upstream).
export async function mount(el, _params, ctx) {
  const frame = h('div', { class: 'live-frame' });
  const state = h('span', { class: 'muted' });
  el.append(h('header', { class: 'view-head' }, h('h1', {}, 'Live'), state), frame);
  let img = null;
  // Level it like the renders do ([image] rotation + level_deg, cropped so there are no black corners)
  let transform = '';
  try {
    const c = (await api('/api/status')).config;
    const t = (c.level_deg * Math.PI) / 180;
    const r = Math.max(c.output_width / c.output_height, c.output_height / c.output_width);
    const scale = Math.abs(Math.cos(t)) + r * Math.abs(Math.sin(t));
    transform = `rotate(${c.rotation - c.level_deg}deg) scale(${scale.toFixed(4)})`;
    frame.style.setProperty('--ar', `${c.output_width} / ${c.output_height}`);
    frame.style.setProperty('--arn', String(c.output_width / c.output_height));
  } catch { /* show it unleveled */ }

  function start() {
    if (img) return;
    const me = h('img', { src: `/live.mjpg?t=${Date.now()}`, alt: 'live camera', style: { transform } });
    me.addEventListener('load', () => { if (img === me) state.textContent = 'connected'; }, { once: true });
    me.addEventListener('error', () => {
      if (img !== me) return; // our own abort below
      state.textContent = '';
      stop('The camera stream is unavailable. Switch views and back to retry.');
    });
    img = me;
    fill(frame, img);
    state.textContent = 'connecting…';
  }

  function stop(why = 'Paused while hidden.') {
    if (!img) return;
    const old = img;
    img = null;
    old.src = 'data:,'; // aborts the multipart request
    old.remove();
    fill(frame, h('p', { class: 'muted' }, why));
  }

  if (!ctx.isHidden()) start(); else stop();
  return {
    unmount: () => stop(),
    onVisible: (visible) => (visible ? start() : stop()),
  };
}
