import { api, fill, h, viewHead } from '../lib.js';

// Camera-viewfinder overlay: corner brackets, blinking REC, a ticking clock, a scan line, center cross.
function viewfinder(clock) {
  return h('div', { class: 'viewfinder', 'aria-hidden': 'true' },
    ['tl', 'tr', 'bl', 'br'].map((c) => h('i', { class: `corner ${c}` })),
    h('span', { class: 'rec' }, h('i'), 'REC'), clock, h('span', { class: 'scan' }), h('span', { class: 'center' }));
}

// Every viewer is another full stream over the Pi's Wi-Fi, so the <img> only exists while this view is showing and
// the window is visible; switching views, minimizing or hiding the tab drops it (the server closes the upstream).
export async function mount(el, _params, ctx) {
  const frame = h('div', { class: 'live-frame' });
  const state = h('span', { class: 'live-pill' }, h('i', { class: 'live-dot' }), h('span', {}, 'connecting…'));
  const setState = (text, on = false) => { state.lastChild.textContent = text; state.classList.toggle('on', on); };
  el.append(viewHead(state, h('span', { class: 'muted' }, 'Connected only while this view is showing')), h('div', { class: 'live-wrap' }, frame));
  let img = null;
  const clock = h('span', { class: 'clock' });
  let clockTimer = null;
  // Level it like the renders do ([image] rotation + level_deg, cropped so there are no black corners)
  let transform = '';
  let src = null;
  try { src = (await api('/api/status')).live.mjpeg; } catch { /* no stream URL: say so below */ }
  try {
    const c = await api('/api/desktop/info');
    const t = (c.level_deg * Math.PI) / 180;
    const r = Math.max(c.output_width / c.output_height, c.output_height / c.output_width);
    const scale = Math.abs(Math.cos(t)) + r * Math.abs(Math.sin(t));
    transform = `rotate(${c.rotation - c.level_deg}deg) scale(${scale.toFixed(4)})`;
    frame.style.setProperty('--ar', `${c.output_width} / ${c.output_height}`);
    frame.style.setProperty('--arn', String(c.output_width / c.output_height));
  } catch { /* show it unleveled */ }

  function start() {
    if (img) return;
    if (!src) { fill(frame, h('p', { class: 'muted' }, 'The live stream is unavailable (the API did not answer).')); return; }
    const me = h('img', { src, alt: 'live camera', style: { transform } });
    me.addEventListener('load', () => { if (img === me) setState('LIVE', true); }, { once: true });
    me.addEventListener('error', () => {
      if (img !== me) return; // our own abort below
      stop('The camera stream is unavailable. Switch views and back to retry.', 'unavailable');
    });
    img = me;
    const tick = () => { clock.textContent = new Date().toLocaleTimeString([], { hour12: false }); };
    tick();
    clearInterval(clockTimer);
    clockTimer = setInterval(tick, 1000);
    fill(frame, img, viewfinder(clock));
    setState('connecting…');
  }

  function stop(why = 'Paused while hidden.', label = 'paused') {
    clearInterval(clockTimer);
    if (!img) return;
    const old = img;
    img = null;
    old.src = 'data:,'; // aborts the multipart request
    old.remove();
    fill(frame, h('p', { class: 'muted' }, why));
    setState(label);
  }

  if (!ctx.isHidden()) start(); else stop();
  return {
    unmount: () => stop(),
    onVisible: (visible) => (visible ? start() : stop()),
  };
}
