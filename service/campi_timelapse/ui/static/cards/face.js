// Card faces drawn on 2D canvases (then used as textures): the face (RGBA; alpha 0 where the 3D art shows through),
// a mask for the foil shader (R = art window, G = foil allowed (0 under text), B = outer border), the art backdrop
// and the card back. Units are millimetres on a 63 x 88 mm card.
import { bodyStyle, cardType, STYLE_NAMES } from './styles.js';

export const CARD_W = 63, CARD_H = 88;
const DISPLAY = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif';
const TEXT = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif';

// Art window (mm) per layout; uv rect is derived from it
export function artRect(full) {
  return full ? { x: 1.4, y: 1.4, w: CARD_W - 2.8, h: CARD_H - 2.8, r: 2.2 } : { x: 5.2, y: 10.4, w: 52.6, h: 35.6, r: 0.7 };
}

function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function canvas(px, aspect = CARD_H / CARD_W) {
  const c = document.createElement('canvas');
  c.width = px; c.height = Math.round(px * aspect);
  return c;
}

const HOURS = [[5, 9, 'Morning Commute'], [10, 14, 'Midday Cruise'], [15, 18, 'Rush Hour'], [19, 22, 'Night Drive']];
function moves(item) {
  const h = item.peak_hour;
  const m1 = h == null ? 'Drive-By' : (HOURS.find(([a, b]) => h >= a && h <= b) || [0, 0, 'Graveyard Shift'])[2];
  const when = h == null ? 'Seen around the clock.' : `Seen most around ${((h + 11) % 12) + 1} ${h < 12 ? 'AM' : 'PM'}.`;
  const lr = item.directions?.LR || 0, rl = item.directions?.RL || 0, n = lr + rl;
  const right = lr >= rl;
  const pct = n ? Math.round((Math.max(lr, rl) / n) * 100) : 0;
  const m2 = !n ? 'Parked' : pct < 60 ? 'Two-Way Traffic' : right ? 'Drift Right' : 'Drift Left';
  const d2 = !n ? 'Never caught moving.' : pct < 60 ? `Heads both ways: ${lr} right, ${rl} left.` : `Heads ${right ? 'right' : 'left'} on ${pct}% of passes.`;
  return [
    { name: m1, text: `${when} ${item.count} sighting${item.count === 1 ? '' : 's'} on the board.`, dmg: Math.min(item.count * 10, 990), cost: 1 + (item.count > 20) + (item.count > 60) },
    { name: m2, text: d2, dmg: item.best_confidence != null ? Math.round(item.best_confidence * 100) : 0, cost: 2 },
  ];
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—');
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '—');

function glyph(ctx, kind, cx, cy, r) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(r, r);
  ctx.beginPath();
  switch (kind) {
    case 'drop': ctx.moveTo(0, -0.62); ctx.bezierCurveTo(0.5, 0, 0.42, 0.55, 0, 0.55); ctx.bezierCurveTo(-0.42, 0.55, -0.5, 0, 0, -0.62); break;
    case 'leaf': ctx.ellipse(0, 0, 0.28, 0.6, 0.6, 0, Math.PI * 2); break;
    case 'flame': ctx.moveTo(0, -0.62); ctx.bezierCurveTo(0.55, -0.1, 0.45, 0.55, 0, 0.55); ctx.bezierCurveTo(-0.45, 0.55, -0.4, 0.05, -0.12, -0.15); ctx.bezierCurveTo(-0.05, 0.1, 0.12, -0.2, 0, -0.62); break;
    case 'bolt': ctx.moveTo(0.12, -0.62); ctx.lineTo(-0.35, 0.08); ctx.lineTo(0, 0.08); ctx.lineTo(-0.12, 0.62); ctx.lineTo(0.35, -0.08); ctx.lineTo(0, -0.08); ctx.closePath(); break;
    case 'fist': ctx.roundRect(-0.42, -0.36, 0.84, 0.72, 0.2); break;
    case 'eye': ctx.ellipse(0, 0, 0.58, 0.32, 0, 0, Math.PI * 2); break;
    case 'mountain': ctx.moveTo(-0.6, 0.45); ctx.lineTo(-0.15, -0.4); ctx.lineTo(0.08, 0); ctx.lineTo(0.25, -0.2); ctx.lineTo(0.6, 0.45); ctx.closePath(); break;
    case 'gear': for (let i = 0; i < 16; i++) { const a = (i / 16) * Math.PI * 2, rad = i % 2 ? 0.42 : 0.6; ctx.lineTo(Math.cos(a) * rad, Math.sin(a) * rad); } ctx.closePath(); break;
    default: for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + (i / 10) * Math.PI * 2, rad = i % 2 ? 0.25 : 0.6; ctx.lineTo(Math.cos(a) * rad, Math.sin(a) * rad); } ctx.closePath();
  }
  ctx.fillStyle = '#fff';
  ctx.fill();
  ctx.restore();
}

function energy(ctx, type, cx, cy, r) {
  const g = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.35, r * 0.1, cx, cy, r);
  g.addColorStop(0, type.light); g.addColorStop(0.55, type.deep); g.addColorStop(1, type.accent);
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = g; ctx.fill();
  ctx.lineWidth = r * 0.12; ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.stroke();
  glyph(ctx, type.glyph, cx, cy, r * 0.95);
}

function rarity(ctx, symbol, cx, cy, s, light) {
  ctx.save();
  const star = (x, y, r, fill) => {
    ctx.beginPath();
    for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + (i / 10) * Math.PI * 2, rad = i % 2 ? r * 0.45 : r; ctx.lineTo(x + Math.cos(a) * rad, y + Math.sin(a) * rad); }
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  };
  const ink = light ? '#fff' : '#111';
  if (symbol === 'circle') { ctx.beginPath(); ctx.arc(cx, cy, s * 0.42, 0, Math.PI * 2); ctx.fillStyle = ink; ctx.fill(); }
  else if (symbol === 'diamond') { ctx.beginPath(); ctx.moveTo(cx, cy - s * 0.55); ctx.lineTo(cx + s * 0.42, cy); ctx.lineTo(cx, cy + s * 0.55); ctx.lineTo(cx - s * 0.42, cy); ctx.closePath(); ctx.fillStyle = ink; ctx.fill(); }
  else if (symbol === 'star') star(cx, cy, s * 0.6, ink);
  else if (symbol === 'double-star') { star(cx - s * 0.45, cy, s * 0.55, ink); star(cx + s * 0.45, cy, s * 0.55, ink); }
  else if (symbol === 'gold-star') {
    const g = ctx.createLinearGradient(cx - s, cy - s, cx + s, cy + s);
    g.addColorStop(0, '#fff3b0'); g.addColorStop(0.5, '#e2a722'); g.addColorStop(1, '#fff1a6');
    star(cx, cy, s * 0.7, g);
  }
  ctx.restore();
}

function wrap(ctx, text, maxW) {
  const words = text.split(' ');
  const lines = [];
  let cur = '';
  for (const w of words) {
    const t = cur ? `${cur} ${w}` : w;
    if (ctx.measureText(t).width > maxW && cur) { lines.push(cur); cur = w; } else cur = t;
  }
  if (cur) lines.push(cur);
  return lines;
}

const SYMBOL = { plain: 'circle', reverse: 'diamond', holo: 'star', fullart: 'double-star', sir: 'gold-star' };

// item: an /api/cards item; opts: {px, total, gpu}
export function drawFace(item, opts = {}) {
  const px = opts.px || 750;
  const finish = item.finish || 'none';
  const full = finish === 'fullart' || finish === 'sir';
  const style = bodyStyle(item);
  const type = cardType(item, style);
  const face = canvas(px), mask = canvas(Math.round(px / 2));
  const f = face.getContext('2d'), m = mask.getContext('2d');
  const k = px / CARD_W;
  f.scale(k, k); m.scale(k / 2, k / 2);
  const caught = item.count > 0;
  const ink = full ? '#fff' : '#15161a';

  // ---- mask base: foil allowed everywhere, border in B, art window in R
  m.fillStyle = 'rgb(0,255,0)'; m.fillRect(0, 0, CARD_W, CARD_H);
  m.globalCompositeOperation = 'lighter';
  const border = full ? 1.4 : 2.6;
  m.fillStyle = 'rgb(0,0,255)'; rr(m, 0, 0, CARD_W, CARD_H, 3); m.fill();
  m.globalCompositeOperation = 'destination-out'; rr(m, border, border, CARD_W - border * 2, CARD_H - border * 2, 1.8); m.fill();
  m.globalCompositeOperation = 'destination-over'; m.fillStyle = 'rgb(0,255,0)'; m.fillRect(0, 0, CARD_W, CARD_H);
  m.globalCompositeOperation = 'lighter';
  const art = artRect(full);
  m.fillStyle = 'rgb(255,0,0)'; rr(m, art.x, art.y, art.w, art.h, art.r); m.fill();
  m.globalCompositeOperation = 'source-over';

  // ---- border + panel
  rr(f, 0, 0, CARD_W, CARD_H, 3);
  if (full) {
    const g = f.createLinearGradient(0, 0, CARD_W, CARD_H);
    if (finish === 'sir') { g.addColorStop(0, '#f6e7a8'); g.addColorStop(0.5, '#b88a2c'); g.addColorStop(1, '#fbeeb8'); }
    else { g.addColorStop(0, '#e8ecf2'); g.addColorStop(0.5, '#8d96a3'); g.addColorStop(1, '#f2f4f7'); }
    f.fillStyle = g; f.fill();
    f.save(); rr(f, border, border, CARD_W - border * 2, CARD_H - border * 2, 1.8); f.clip();
    f.clearRect(0, 0, CARD_W, CARD_H);
    // legibility: dark fades top and bottom over the art
    const top = f.createLinearGradient(0, 0, 0, 18);
    top.addColorStop(0, 'rgba(6,8,14,.78)'); top.addColorStop(0.55, 'rgba(6,8,14,.45)'); top.addColorStop(1, 'rgba(6,8,14,0)');
    f.fillStyle = top; f.fillRect(0, 0, CARD_W, 18);
    const bot = f.createLinearGradient(0, 46, 0, CARD_H);
    bot.addColorStop(0, 'rgba(6,8,14,0)'); bot.addColorStop(0.35, 'rgba(6,8,14,.55)'); bot.addColorStop(1, 'rgba(6,8,14,.82)');
    f.fillStyle = bot; f.fillRect(0, 46, CARD_W, CARD_H - 46);
    f.restore();
  } else {
    const g = f.createLinearGradient(0, 0, CARD_W, CARD_H);
    if (caught) { g.addColorStop(0, '#fbe680'); g.addColorStop(0.45, '#e9bf3c'); g.addColorStop(1, '#f7dc6a'); }
    else { g.addColorStop(0, '#9da2ab'); g.addColorStop(1, '#6d727b'); }
    f.fillStyle = g; f.fill();
    rr(f, border, border, CARD_W - border * 2, CARD_H - border * 2, 1.8);
    const p = f.createLinearGradient(0, 0, 0, CARD_H);
    p.addColorStop(0, caught ? type.light : '#c9ccd2'); p.addColorStop(0.55, caught ? mix(type.light, type.deep, 0.35) : '#a7abb3'); p.addColorStop(1, caught ? mix(type.light, type.deep, 0.6) : '#8f939b');
    f.fillStyle = p; f.fill();
    // fine diagonal texture
    f.save(); f.clip(); f.globalAlpha = 0.07; f.strokeStyle = '#000'; f.lineWidth = 0.18;
    for (let x = -CARD_H; x < CARD_W; x += 1.1) { f.beginPath(); f.moveTo(x, CARD_H); f.lineTo(x + CARD_H, 0); f.stroke(); }
    f.restore();
    // art window: punch it out, then a metallic frame around it
    f.save(); rr(f, art.x, art.y, art.w, art.h, art.r); f.clip(); f.clearRect(0, 0, CARD_W, CARD_H); f.restore();
    const fr = f.createLinearGradient(art.x, art.y, art.x + art.w, art.y + art.h);
    fr.addColorStop(0, '#fff7d0'); fr.addColorStop(0.3, '#c9a43c'); fr.addColorStop(0.6, '#fff2b8'); fr.addColorStop(1, '#a8811f');
    f.lineWidth = 0.9; f.strokeStyle = fr; rr(f, art.x - 0.45, art.y - 0.45, art.w + 0.9, art.h + 0.9, art.r + 0.4); f.stroke();
  }

  // text helper: draws on the face and knocks the foil out under it in the mask
  const T = (text, x, y, { size = 2, weight = 600, font = TEXT, color = ink, align = 'left', italic = false, shadow = full, fill = null } = {}) => {
    const spec = `${italic ? 'italic ' : ''}${weight} ${size}px ${font}`;
    for (const [ctx, isMask] of [[f, false], [m, true]]) {
      ctx.save();
      ctx.font = spec; ctx.textAlign = align; ctx.textBaseline = 'alphabetic';
      if (isMask) { ctx.fillStyle = '#000'; ctx.lineWidth = size * 0.18; ctx.strokeStyle = '#000'; ctx.strokeText(text, x, y); }
      else {
        if (shadow) { ctx.shadowColor = 'rgba(0,0,0,.65)'; ctx.shadowBlur = size * 0.5; ctx.shadowOffsetY = size * 0.06; }
        ctx.fillStyle = fill || color;
      }
      ctx.fillText(text, x, y);
      ctx.restore();
    }
    f.save(); f.font = spec; const w = f.measureText(text).width; f.restore();
    return w;
  };

  // ---- header: make pill, name, SEEN count, energy
  const make = (item.generic ? 'TYPE' : (item.make || '')).toUpperCase();
  f.save(); f.font = `800 1.9px ${TEXT}`; const pw = f.measureText(make).width + 2.4; f.restore();
  const pill = f.createLinearGradient(4.2, 0, 4.2 + pw, 0);
  pill.addColorStop(0, full ? 'rgba(255,255,255,.22)' : '#f4f5f7'); pill.addColorStop(1, full ? 'rgba(255,255,255,.08)' : '#c5c9d0');
  rr(f, 4.0, 3.6, pw, 2.6, 1.3); f.fillStyle = pill; f.fill();
  T(make, 4.0 + pw / 2, 5.5, { size: 1.9, weight: 800, align: 'center', shadow: false, color: full ? '#fff' : '#30333a' });
  const name = caught || !item.label ? (item.generic ? cap(item.label) : (item.model || item.label)) : (item.generic ? cap(item.label) : (item.model || item.label));
  let ns = 5.2;
  f.save(); f.font = `800 ${ns}px ${DISPLAY}`; while (f.measureText(name).width > 33 && ns > 3) { ns -= 0.2; f.font = `800 ${ns}px ${DISPLAY}`; } f.restore();
  let nameFill = null;
  if (finish === 'sir') { nameFill = f.createLinearGradient(0, 4, 0, 10); nameFill.addColorStop(0, '#fff8d6'); nameFill.addColorStop(1, '#f0c24a'); }
  T(name, 4.2, 9.4, { size: ns, weight: 800, font: DISPLAY, fill: nameFill });
  energy(f, type, 58.2, 6.6, 2.35);
  const cw = T(String(item.count), 55.2, 9.2, { size: 5.0, weight: 800, font: DISPLAY, align: 'right' });
  T('SEEN', 55.2 - cw - 0.7, 9.0, { size: 1.7, weight: 800, align: 'right' });

  // ---- info strip
  const stripY = full ? 52.5 : 47.4;
  if (!full) {
    const sg = f.createLinearGradient(7, 0, 56, 0);
    sg.addColorStop(0, 'rgba(255,255,255,0)'); sg.addColorStop(0.15, '#e9ebef'); sg.addColorStop(0.85, '#c9cdd4'); sg.addColorStop(1, 'rgba(255,255,255,0)');
    f.save(); f.beginPath(); f.moveTo(9, stripY); f.lineTo(57, stripY); f.lineTo(55, stripY + 2.7); f.lineTo(7, stripY + 2.7); f.closePath(); f.fillStyle = sg; f.fill(); f.restore();
  }
  const bits = [`No. ${String(item.number || 0).padStart(3, '0')}`, STYLE_NAMES[style] || 'Car', item.year_range, item.color?.known ? cap(item.color.name) : null].filter(Boolean);
  T(bits.join('  ·  '), CARD_W / 2, stripY + 2.0, { size: 1.65, weight: 600, italic: true, align: 'center', color: full ? '#e8eaf0' : '#30333a' });

  // ---- moves
  let y = full ? 59 : 55;
  for (const mv of moves(item)) {
    for (let i = 0; i < mv.cost; i++) energy(f, i === mv.cost - 1 && mv.cost > 1 ? { ...type, light: '#f4f4f4', deep: '#b9bcc2', accent: '#7d8188', glyph: 'star' } : type, 6.2 + i * 3.3, y - 1.1, 1.45);
    T(mv.name, 16.2, y, { size: 3.1, weight: 800, font: DISPLAY });
    T(String(mv.dmg), 58.5, y + 0.2, { size: 3.6, weight: 800, font: DISPLAY, align: 'right' });
    f.save(); f.font = `500 1.75px ${TEXT}`; const lines = wrap(f, mv.text, 44); f.restore();
    lines.slice(0, 2).forEach((ln, i) => T(ln, 16.2, y + 2.6 + i * 2.2, { size: 1.75, weight: 500 }));
    y += full ? 8.6 : 9.2;
  }

  // ---- stats row
  const sy = full ? 77.4 : 75.8;
  f.save(); f.globalAlpha = 0.45; f.strokeStyle = ink; f.lineWidth = 0.15; f.beginPath(); f.moveTo(5, sy - 3.2); f.lineTo(58, sy - 3.2); f.stroke(); f.restore();
  const cols = [['first seen', fmtDate(item.first_seen_at)], ['last seen', caught ? `${fmtDate(item.last_seen_at)} ${fmtTime(item.last_seen_at)}` : '—'], ['best match', item.best_confidence != null ? `${Math.round(item.best_confidence * 100)}%` : '—']];
  cols.forEach(([c, v], i) => {
    const x = 6 + i * 18.2;
    T(c.toUpperCase(), x, sy - 1.1, { size: 1.3, weight: 700, color: full ? '#c9ccd6' : '#4b4f58' });
    T(v, x, sy + 1.4, { size: 2.0, weight: 700 });
  });

  // ---- footer
  const fy = CARD_H - (full ? 3.1 : 3.9);
  T(`Illus. campi · ${opts.gpu || 'WebGL'}`, 5.2, fy, { size: 1.35, weight: 600, italic: true });
  const setNo = `${String(item.number || 0).padStart(3, '0')}/${String(opts.total || 0).padStart(3, '0')}`;
  T(setNo, 40.5, fy, { size: 1.45, weight: 700, align: 'right' });
  if (item.finish) rarity(f, SYMBOL[item.finish], 43.2, fy - 0.55, 2.0, full);
  T('© campi 2026', 58, fy, { size: 1.3, weight: 600, align: 'right' });

  return { face, mask, art, full, type, style };
}

function cap(s) { return (s || '').replace(/\b\w/g, (c) => c.toUpperCase()); }
function mix(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(',')})`;
}

// Art backdrop behind the car: an illustrated sky/skyline/road for standard cards, a speed-line burst for full art,
// the real street frame (painterly) for SIR once it loads.
export function drawBackdrop(item, type, finish, img = null) {
  const c = canvas(1024, 1.25);
  const x = c.getContext('2d');
  const W = c.width, H = c.height;
  if (finish === 'sir' && img) {
    const s = Math.max(W / img.width, H / img.height) * 1.08;
    x.filter = 'saturate(1.55) contrast(1.12) brightness(1.06) blur(1.2px)';
    x.drawImage(img, (W - img.width * s) / 2, H * 0.62 - img.height * s * 0.6, img.width * s, img.height * s);
    x.filter = 'none';
    const glow = x.createRadialGradient(W * 0.5, H * 0.45, 10, W * 0.5, H * 0.45, W * 0.8);
    glow.addColorStop(0, 'rgba(255,240,200,.25)'); glow.addColorStop(1, 'rgba(40,20,80,.35)');
    x.fillStyle = glow; x.fillRect(0, 0, W, H);
    return c;
  }
  if (finish === 'fullart' || finish === 'sir') {
    const g = x.createRadialGradient(W / 2, H * 0.48, 10, W / 2, H * 0.48, W);
    g.addColorStop(0, '#ffffff'); g.addColorStop(0.18, type.light); g.addColorStop(0.55, type.deep); g.addColorStop(1, type.accent);
    x.fillStyle = g; x.fillRect(0, 0, W, H);
    x.save(); x.translate(W / 2, H * 0.48);
    for (let i = 0; i < 90; i++) {
      const a = (i / 90) * Math.PI * 2 + Math.random() * 0.03;
      x.rotate(a - (i ? (i - 1) / 90 * Math.PI * 2 : 0));
      x.globalAlpha = 0.08 + Math.random() * 0.12;
      x.fillStyle = i % 2 ? '#fff' : type.light;
      x.beginPath(); x.moveTo(40, -2); x.lineTo(W, -14 - Math.random() * 20); x.lineTo(W, 14 + Math.random() * 20); x.lineTo(40, 2); x.fill();
    }
    x.restore();
    for (let i = 0; i < 60; i++) {
      x.globalAlpha = 0.1 + Math.random() * 0.25;
      x.fillStyle = '#fff';
      x.beginPath(); x.arc(Math.random() * W, Math.random() * H, 3 + Math.random() * 22, 0, Math.PI * 2); x.fill();
    }
    x.globalAlpha = 1;
    return c;
  }
  // sky
  const sky = x.createLinearGradient(0, 0, 0, H * 0.62);
  sky.addColorStop(0, mix(type.deep, '#0b1030', 0.35)); sky.addColorStop(0.55, type.deep); sky.addColorStop(1, type.light);
  x.fillStyle = sky; x.fillRect(0, 0, W, H);
  const sun = x.createRadialGradient(W * 0.68, H * 0.38, 4, W * 0.68, H * 0.38, W * 0.36);
  sun.addColorStop(0, 'rgba(255,250,230,.95)'); sun.addColorStop(0.12, 'rgba(255,240,200,.6)'); sun.addColorStop(1, 'rgba(255,240,200,0)');
  x.fillStyle = sun; x.fillRect(0, 0, W, H);
  // skyline
  let seed = [...(item.label || 'x')].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  for (const [alpha, base, hmax] of [[0.28, 0.6, 0.3], [0.5, 0.62, 0.18]]) {
    x.fillStyle = type.accent; x.globalAlpha = alpha;
    let bx = 0;
    while (bx < W) { const bw = 30 + rnd() * 70, bh = H * (0.04 + rnd() * hmax); x.fillRect(bx, H * base - bh, bw - 4, bh + 2); bx += bw; }
  }
  x.globalAlpha = 1;
  // ground haze
  const haze = x.createLinearGradient(0, H * 0.5, 0, H * 0.64);
  haze.addColorStop(0, 'rgba(255,255,255,0)'); haze.addColorStop(1, 'rgba(255,255,255,.35)');
  x.fillStyle = haze; x.fillRect(0, H * 0.5, W, H * 0.14);
  return c;
}

// Road/ground texture under the car (fades to transparent at the edges)
export function drawGround(type, full) {
  const c = canvas(512, 1);
  const x = c.getContext('2d');
  const g = x.createRadialGradient(256, 256, 30, 256, 256, 256);
  const base = full ? 'rgba(20,22,30,' : 'rgba(46,48,56,';
  g.addColorStop(0, `${base}0.95)`); g.addColorStop(0.7, `${base}0.7)`); g.addColorStop(1, `${base}0)`);
  x.fillStyle = g; x.fillRect(0, 0, 512, 512);
  x.globalAlpha = 0.5; x.fillStyle = full ? type.light : '#f2f2f2';
  for (let i = 0; i < 8; i++) x.fillRect(i * 70 - 10, 250, 38, 7);
  return c;
}

// Card back (shared by every card)
let backCanvas = null;
export function drawBack(px = 750) {
  if (backCanvas) return backCanvas;
  const c = canvas(px), x = c.getContext('2d');
  const k = px / CARD_W;
  x.scale(k, k);
  rr(x, 0, 0, CARD_W, CARD_H, 3); x.fillStyle = '#1b2a6b'; x.fill();
  rr(x, 2.4, 2.4, CARD_W - 4.8, CARD_H - 4.8, 2); x.save(); x.clip();
  const g = x.createRadialGradient(CARD_W / 2, CARD_H / 2, 2, CARD_W / 2, CARD_H / 2, 60);
  g.addColorStop(0, '#5b7cff'); g.addColorStop(0.45, '#2440a8'); g.addColorStop(1, '#0d1640');
  x.fillStyle = g; x.fillRect(0, 0, CARD_W, CARD_H);
  x.globalAlpha = 0.18; x.strokeStyle = '#9fc0ff'; x.lineWidth = 0.35;
  for (let i = 0; i < 26; i++) { x.beginPath(); x.ellipse(CARD_W / 2, CARD_H / 2, 4 + i * 2.4, 3 + i * 2.9, i * 0.18, 0, Math.PI * 2); x.stroke(); }
  x.globalAlpha = 1;
  x.restore();
  // lens roundel
  const cx = CARD_W / 2, cy = CARD_H / 2;
  const ring = x.createLinearGradient(cx - 14, cy - 14, cx + 14, cy + 14);
  ring.addColorStop(0, '#ff7a59'); ring.addColorStop(0.5, '#ff4d8d'); ring.addColorStop(1, '#8b5cf6');
  x.beginPath(); x.arc(cx, cy, 14, 0, Math.PI * 2); x.fillStyle = ring; x.fill();
  x.beginPath(); x.arc(cx, cy, 10.5, 0, Math.PI * 2); x.fillStyle = '#0c0f22'; x.fill();
  const lens = x.createRadialGradient(cx - 3, cy - 3, 0.5, cx, cy, 9);
  lens.addColorStop(0, '#d4f5ff'); lens.addColorStop(0.25, '#22d3ee'); lens.addColorStop(1, '#101a4a');
  x.beginPath(); x.arc(cx, cy, 8.2, 0, Math.PI * 2); x.fillStyle = lens; x.fill();
  x.font = `900 7px ${DISPLAY}`; x.textAlign = 'center'; x.fillStyle = '#fff';
  x.shadowColor = 'rgba(0,0,0,.6)'; x.shadowBlur = 1.5;
  x.fillText('CAMPI', cx, cy - 19);
  x.font = `700 2.4px ${TEXT}`; x.fillText('TRADING CAR GAME', cx, cy + 22);
  backCanvas = c;
  return c;
}
