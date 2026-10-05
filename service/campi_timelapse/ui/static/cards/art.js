// Illustrated scenery for the cards: one themed landscape per card type (city at dusk, suburb, desert, race track,
// neon night, harbour, mountains, storm, building site), painted in flat layers with atmospheric haze. The same
// painter fills a card's art backdrop (one canvas) and the inspector background (separate depth layers for parallax).
// Canvases are cached by theme + mood + variant, so a grid full of cards paints each scene once.

const THEMES = {
  commuter: city('dusk'), family: suburb, hauler: desert, speed: track, city: city('neon'),
  cargo: harbour, trail: mountains, volt: storm, heavy: site,
};

// ------------------------------------------------------------------ helpers

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}
const lerp = (a, b, t) => a + (b - a) * t;
function hex(c) { return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)); }
function mix(a, b, t) {
  const [x, y] = [hex(a), hex(b)];
  return `#${x.map((v, i) => Math.round(lerp(v, y[i], t)).toString(16).padStart(2, '0')).join('')}`;
}
function rgba(c, a) { const [r, g, b] = hex(c); return `rgba(${r},${g},${b},${a})`; }

// 1D value noise for ridgelines
function noise1(rnd, n = 64) {
  const v = Array.from({ length: n + 1 }, rnd);
  return (x) => {
    let s = 0, a = 0.5, f = 1;
    for (let o = 0; o < 5; o++) {
      const p = (x * f * 6 + o * 13.7) % n, i = Math.floor(p), t = p - i, u = t * t * (3 - 2 * t);
      s += a * lerp(v[i], v[i + 1], u); a *= 0.5; f *= 2.03;
    }
    return s;
  };
}

function vgrad(x, y0, y1, stops) {
  const g = x.createLinearGradient(0, y0, 0, y1);
  stops.forEach(([o, c]) => g.addColorStop(o, c));
  return g;
}

function glow(x, cx, cy, r, color, a = 1) {
  const g = x.createRadialGradient(cx, cy, 0, cx, cy, r);
  g.addColorStop(0, rgba(color, a)); g.addColorStop(0.25, rgba(color, a * 0.45)); g.addColorStop(1, rgba(color, 0));
  x.fillStyle = g; x.fillRect(cx - r, cy - r, r * 2, r * 2);
}

function cloud(x, cx, cy, s, top, bottom, rnd) {
  x.save();
  x.fillStyle = vgrad(x, cy - s * 0.9, cy + s * 0.15, [[0, top], [1, bottom]]);
  x.beginPath();
  const n = 5 + Math.floor(rnd() * 4);
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1), px = cx + (t - 0.5) * s * 2.6, r = s * (0.35 + Math.sin(t * Math.PI) * 0.45 + rnd() * 0.15);
    x.moveTo(px + r, cy); x.arc(px, cy - r * 0.35, r, 0, Math.PI * 2);
  }
  x.fill();
  x.fillRect(cx - s * 1.4, cy - s * 0.1, s * 2.8, s * 0.12);
  x.restore();
}

function clouds(x, W, rnd, y0, y1, n, top, bottom, scale) {
  for (let i = 0; i < n; i++) cloud(x, rnd() * W, lerp(y0, y1, rnd()), scale * (0.6 + rnd() * 0.8), top, bottom, rnd);
}

function stars(x, W, H, rnd, n, color = '#ffffff') {
  for (let i = 0; i < n; i++) {
    const r = rnd() < 0.08 ? 1.6 : 0.7 + rnd() * 0.6;
    x.fillStyle = rgba(color, 0.35 + rnd() * 0.6);
    x.beginPath(); x.arc(rnd() * W, rnd() * H, r * (W / 1024), 0, Math.PI * 2); x.fill();
  }
}

// a ridgeline from fbm noise, filled down to `floor`
function ridge(x, W, rnd, base, amp, fill, { floor = null, sharp = 1, snow = null, flat = 0 } = {}) {
  const f = noise1(rnd);
  const pts = [];
  for (let i = 0; i <= 160; i++) {
    const t = i / 160;
    let v = f(t);
    if (sharp !== 1) v = Math.pow(v, sharp);
    if (flat) v = Math.min(v, flat) + Math.max(0, v - flat) * 0.08; // mesa tops
    pts.push([t * W, base - v * amp]);
  }
  x.beginPath(); x.moveTo(0, floor ?? base + amp);
  pts.forEach(([px, py]) => x.lineTo(px, py));
  x.lineTo(W, floor ?? base + amp); x.closePath();
  x.fillStyle = fill; x.fill();
  if (snow) {
    x.save(); x.clip();
    // caps only above a ragged snowline
    const line = base - amp * 0.55;
    x.beginPath(); x.moveTo(0, 0); x.lineTo(W, 0);
    for (let i = 160; i >= 0; i--) { const px = (i / 160) * W; x.lineTo(px, line + Math.sin(px * 0.09) * amp * 0.05 + Math.sin(px * 0.023) * amp * 0.06 + (i % 3) * amp * 0.012); }
    x.closePath();
    x.fillStyle = snow; x.fill();
    x.restore();
  }
  return pts;
}

function hills(x, W, base, amp, waves, phase, fill, floor) {
  x.beginPath(); x.moveTo(0, floor);
  for (let i = 0; i <= 120; i++) {
    const t = i / 120;
    x.lineTo(t * W, base - amp * (0.55 + 0.3 * Math.sin(t * waves * Math.PI * 2 + phase) + 0.15 * Math.sin(t * waves * 5.3 + phase * 2)));
  }
  x.lineTo(W, floor); x.closePath();
  x.fillStyle = fill; x.fill();
}

function skyline(x, W, rnd, base, hmin, hmax, body, lit, { litFrac = 0.35, wmin = 0.03, wmax = 0.08, antenna = 0.2, neon = null } = {}) {
  const k = W / 1024;
  let bx = -rnd() * 30 * k;
  while (bx < W) {
    const bw = W * lerp(wmin, wmax, rnd()), bh = lerp(hmin, hmax, Math.pow(rnd(), 1.4));
    const top = base - bh;
    x.fillStyle = body;
    x.fillRect(bx, top, bw - 2 * k, bh + 2);
    if (rnd() < 0.35) x.fillRect(bx + bw * 0.2, top - bh * 0.08, bw * 0.55, bh * 0.08 + 1); // setback
    if (rnd() < antenna) x.fillRect(bx + bw * 0.45, top - bh * 0.22, 2 * k, bh * 0.22);
    if (lit) {
      const cw = 5 * k, chh = 7 * k;
      for (let wy = top + 8 * k; wy < base - 6 * k; wy += chh * 1.7) {
        for (let wx = bx + 5 * k; wx < bx + bw - 8 * k; wx += cw * 1.9) {
          if (rnd() > litFrac) continue;
          x.fillStyle = rgba(lit, 0.45 + rnd() * 0.5);
          x.fillRect(wx, wy, cw, chh);
        }
      }
    }
    if (neon && rnd() < 0.3) {
      const c = neon[Math.floor(rnd() * neon.length)];
      x.save(); x.shadowColor = c; x.shadowBlur = 14 * k; x.strokeStyle = c; x.lineWidth = 2.5 * k;
      const ny = top + bh * (0.15 + rnd() * 0.3);
      x.strokeRect(bx + bw * 0.15, ny, bw * 0.6, 14 * k);
      x.restore();
    }
    bx += bw;
  }
}

function pine(x, px, base, h, fill) {
  x.fillStyle = fill;
  x.beginPath();
  for (let i = 0; i < 4; i++) {
    const y = base - h * (0.18 + i * 0.22), w = h * (0.32 - i * 0.06);
    x.moveTo(px - w, y + h * 0.26); x.lineTo(px, y - h * 0.08); x.lineTo(px + w, y + h * 0.26);
  }
  x.fill();
  x.fillRect(px - h * 0.025, base - h * 0.2, h * 0.05, h * 0.2);
}

function roundTree(x, px, base, h, fill, light) {
  x.fillStyle = fill;
  x.fillRect(px - h * 0.04, base - h * 0.45, h * 0.08, h * 0.45);
  x.beginPath();
  x.arc(px, base - h * 0.62, h * 0.3, 0, Math.PI * 2);
  x.arc(px - h * 0.2, base - h * 0.5, h * 0.22, 0, Math.PI * 2);
  x.arc(px + h * 0.2, base - h * 0.52, h * 0.24, 0, Math.PI * 2);
  x.fill();
  if (light) { x.fillStyle = light; x.beginPath(); x.arc(px - h * 0.08, base - h * 0.72, h * 0.14, 0, Math.PI * 2); x.fill(); }
}

function house(x, px, base, w, h, wall, roof, win) {
  x.fillStyle = wall; x.fillRect(px, base - h, w, h);
  x.fillStyle = roof;
  x.beginPath(); x.moveTo(px - w * 0.08, base - h); x.lineTo(px + w / 2, base - h - w * 0.38); x.lineTo(px + w * 1.08, base - h); x.fill();
  x.fillStyle = win;
  x.fillRect(px + w * 0.16, base - h * 0.68, w * 0.2, h * 0.26);
  x.fillRect(px + w * 0.62, base - h * 0.68, w * 0.2, h * 0.26);
}

function crane(x, px, base, h, jib, color, k) {
  x.save();
  x.strokeStyle = color; x.fillStyle = color; x.lineWidth = 2 * k;
  const mw = 9 * k;
  x.strokeRect(px - mw / 2, base - h, mw, h);
  for (let y = base; y > base - h; y -= mw) { x.beginPath(); x.moveTo(px - mw / 2, y); x.lineTo(px + mw / 2, y - mw); x.stroke(); }
  const jy = base - h;
  x.strokeRect(px - jib * 0.28, jy - 6 * k, jib * 1.28, 6 * k);
  for (let jx = px - jib * 0.28; jx < px + jib; jx += 9 * k) { x.beginPath(); x.moveTo(jx, jy); x.lineTo(jx + 4.5 * k, jy - 6 * k); x.lineTo(jx + 9 * k, jy); x.stroke(); }
  x.beginPath(); x.moveTo(px, jy - 26 * k); x.lineTo(px - jib * 0.28, jy - 6 * k); x.moveTo(px, jy - 26 * k); x.lineTo(px + jib * 0.7, jy - 6 * k); x.stroke();
  x.fillRect(px - 2 * k, jy - 26 * k, 4 * k, 20 * k);
  x.fillRect(px - jib * 0.28, jy - 4 * k, jib * 0.16, 14 * k); // counterweight
  x.beginPath(); x.moveTo(px + jib * 0.62, jy); x.lineTo(px + jib * 0.62, jy + h * 0.35); x.lineWidth = 1 * k; x.stroke();
  x.restore();
}

function pylon(x, px, base, h, color, k) {
  x.save(); x.strokeStyle = color; x.lineWidth = 2 * k;
  x.beginPath();
  x.moveTo(px - h * 0.16, base); x.lineTo(px - h * 0.04, base - h); x.lineTo(px + h * 0.04, base - h); x.lineTo(px + h * 0.16, base);
  for (let i = 1; i < 6; i++) { const y = base - (h * i) / 6, w = lerp(0.16, 0.04, i / 6) * h; x.moveTo(px - w, y); x.lineTo(px + w, y + h / 6); x.moveTo(px + w, y); x.lineTo(px - w, y + h / 6); }
  x.moveTo(px - h * 0.22, base - h * 0.78); x.lineTo(px + h * 0.22, base - h * 0.78);
  x.moveTo(px - h * 0.16, base - h * 0.6); x.lineTo(px + h * 0.16, base - h * 0.6);
  x.stroke(); x.restore();
}

function wires(x, pts, color, k, sag) {
  x.save(); x.strokeStyle = color; x.lineWidth = 1 * k;
  for (let i = 0; i < pts.length - 1; i++) {
    const [a, b] = [pts[i], pts[i + 1]];
    x.beginPath(); x.moveTo(a[0], a[1]); x.quadraticCurveTo((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + sag, b[0], b[1]); x.stroke();
  }
  x.restore();
}

function turbine(x, px, base, h, color, ang, k) {
  x.save(); x.fillStyle = color; x.strokeStyle = color;
  x.beginPath(); x.moveTo(px - 3 * k, base); x.lineTo(px - 1.2 * k, base - h); x.lineTo(px + 1.2 * k, base - h); x.lineTo(px + 3 * k, base); x.fill();
  x.translate(px, base - h);
  for (let i = 0; i < 3; i++) {
    x.rotate((Math.PI * 2) / 3);
    x.beginPath(); x.moveTo(0, -2 * k); x.quadraticCurveTo(h * 0.2, -3 * k + ang, h * 0.48, 0); x.quadraticCurveTo(h * 0.2, 3 * k, 0, 2 * k); x.fill();
  }
  x.beginPath(); x.arc(0, 0, 3 * k, 0, Math.PI * 2); x.fill();
  x.restore();
}

function bolt(x, rnd, x0, y0, y1, color, k) {
  const pts = [[x0, y0]];
  let px = x0;
  for (let y = y0; y < y1;) { y += (14 + rnd() * 26) * k; px += (rnd() - 0.5) * 46 * k; pts.push([px, y]); }
  x.save();
  x.lineCap = 'round'; x.lineJoin = 'round';
  for (const [w, a] of [[14, 0.12], [6, 0.35], [2.2, 1]]) {
    x.strokeStyle = a === 1 ? '#fffbe6' : rgba(color, a); x.lineWidth = w * k;
    x.beginPath(); pts.forEach(([a1, b1], i) => (i ? x.lineTo(a1, b1) : x.moveTo(a1, b1))); x.stroke();
  }
  // a branch
  const s = pts[Math.floor(pts.length / 3)];
  x.lineWidth = 1.5 * k; x.strokeStyle = rgba('#fffbe6', 0.8);
  x.beginPath(); x.moveTo(s[0], s[1]);
  let bx = s[0], by = s[1];
  for (let i = 0; i < 4; i++) { bx += (10 + rnd() * 18) * k; by += (8 + rnd() * 18) * k; x.lineTo(bx, by); }
  x.stroke();
  x.restore();
}

function rays(x, cx, cy, W, H, color, alpha, n, rnd) {
  x.save(); x.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (rnd() - 0.5) * Math.PI * 1.3, w = 0.02 + rnd() * 0.05, L = Math.max(W, H) * 1.3;
    const g = x.createRadialGradient(cx, cy, 0, cx, cy, L);
    g.addColorStop(0, rgba(color, alpha)); g.addColorStop(1, rgba(color, 0));
    x.fillStyle = g;
    x.beginPath(); x.moveTo(cx, cy); x.lineTo(cx + Math.cos(a - w) * L, cy + Math.sin(a - w) * L); x.lineTo(cx + Math.cos(a + w) * L, cy + Math.sin(a + w) * L); x.fill();
  }
  x.restore();
}

function bokeh(x, W, H, rnd, n, colors, k) {
  x.save(); x.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const r = (6 + rnd() * 26) * k, px = rnd() * W, py = rnd() * H * 0.7;
    const g = x.createRadialGradient(px, py, r * 0.2, px, py, r);
    const c = colors[i % colors.length];
    g.addColorStop(0, rgba(c, 0.1)); g.addColorStop(0.85, rgba(c, 0.14)); g.addColorStop(1, rgba(c, 0));
    x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, Math.PI * 2); x.fill();
  }
  x.restore();
}

function ground(x, W, H, hz, top, bottom) {
  x.fillStyle = vgrad(x, hz, H, [[0, top], [1, bottom]]);
  x.fillRect(0, hz, W, H - hz);
}

// ------------------------------------------------------------------ themes
// Each theme returns layers [{depth, draw(x, W, H, hz, rnd, P)}]: depth 0 = sky, 1 = far, 2 = mid, 3 = near.
// P is the mood: {golden, drama} adjust the palette and extras.

function city(kind) {
  const neon = kind === 'neon';
  return (P) => {
    const sky = neon ? ['#120a2e', '#3a1460', '#a03a8c', '#ff8a7a'] : P.golden ? ['#2a2d6e', '#6a4c9c', '#f08a5d', '#ffd39a'] : ['#1d3b7a', '#3f6fb8', '#9cc3e8', '#ffd8b0'];
    const far = neon ? '#3b2068' : '#6d8fc4', mid = neon ? '#1d1240' : '#2f4f86', near = neon ? '#0d0a24' : '#1a2b52';
    return [
      { depth: 0, draw(x, W, H, hz, rnd) {
        x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
        if (neon) { stars(x, W, hz * 0.7, rnd, 220); glow(x, W * 0.78, hz * 0.22, W * 0.05, '#fff6e0', 0.95); glow(x, W * 0.78, hz * 0.22, W * 0.2, '#c08cff', 0.25); }
        else { glow(x, W * 0.3, hz * 0.92, W * 0.42, '#fff0c8', 0.85); clouds(x, W, rnd, hz * 0.15, hz * 0.6, 7, rgba('#ffe6f0', 0.85), rgba('#d79ab8', 0.7), W * 0.06); }
        if (neon) for (let i = 0; i < 3; i++) { x.save(); x.globalCompositeOperation = 'lighter'; x.translate(W * (0.2 + i * 0.3), hz); x.rotate(-0.5 + i * 0.45 + rnd() * 0.2); const g = x.createLinearGradient(0, 0, 0, -hz); g.addColorStop(0, 'rgba(160,200,255,.22)'); g.addColorStop(1, 'rgba(160,200,255,0)'); x.fillStyle = g; x.beginPath(); x.moveTo(-6, 0); x.lineTo(-40, -hz * 1.2); x.lineTo(40, -hz * 1.2); x.lineTo(6, 0); x.fill(); x.restore(); }
      } },
      { depth: 1, draw(x, W, H, hz, rnd) { skyline(x, W, rnd, hz, H * 0.08, H * 0.34, far, neon ? '#ffd9f2' : '#fff1c4', { litFrac: neon ? 0.3 : 0.12, wmin: 0.025, wmax: 0.06 }); } },
      { depth: 2, draw(x, W, H, hz, rnd) {
        skyline(x, W, rnd, hz, H * 0.05, H * 0.22, mid, neon ? '#7ff3ff' : '#ffe39a', { litFrac: neon ? 0.45 : 0.3, neon: neon ? ['#ff4dd2', '#35f0ff', '#ffe14d'] : null });
        // elevated highway with lamps
        const k = W / 1024, dy = hz - H * 0.035;
        x.fillStyle = near; x.fillRect(0, dy, W, 7 * k);
        for (let px = 20 * k; px < W; px += 120 * k) { x.fillRect(px, dy, 9 * k, hz - dy); }
        for (let px = 0; px < W; px += 60 * k) { x.fillRect(px, dy - 16 * k, 1.5 * k, 16 * k); glow(x, px + 3 * k, dy - 16 * k, 10 * k, '#ffd27a', 0.9); }
      } },
      { depth: 3, draw(x, W, H, hz) { ground(x, W, H, hz, neon ? '#2a1846' : '#4a5470', neon ? '#0b0818' : '#20242e'); } },
    ];
  };
}

function suburb(P) {
  const sky = P.golden ? ['#3c6fb0', '#9bb8d8', '#ffd59a'] : ['#2f7fd8', '#7fb8ee', '#d6ecff'];
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
      glow(x, W * 0.72, hz * 0.3, W * 0.3, '#fffbe8', 0.9);
      clouds(x, W, rnd, hz * 0.18, hz * 0.62, 6, '#ffffff', rgba('#c9dcf2', 0.95), W * 0.075);
    } },
    { depth: 1, draw(x, W, H, hz) { hills(x, W, hz - H * 0.02, H * 0.12, 1.2, 0.4, '#8fb7c9', hz + 2); hills(x, W, hz, H * 0.08, 2.1, 2.2, '#6fa58a', hz + 2); } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      hills(x, W, hz + H * 0.01, H * 0.05, 3, 1.1, '#4f9a55', hz + 4);
      const k = W / 1024;
      for (let px = -20 * k; px < W; px += (70 + rnd() * 60) * k) {
        if (rnd() < 0.55) house(x, px, hz + 2, (34 + rnd() * 22) * k, (22 + rnd() * 12) * k, ['#f3ead8', '#e8d6c0', '#dfe8f0', '#f6e1c8'][Math.floor(rnd() * 4)], ['#a4553b', '#5f6b7a', '#7b4b3a'][Math.floor(rnd() * 3)], '#8fb7d9');
        else roundTree(x, px + 20 * k, hz + 2, (40 + rnd() * 30) * k, '#2f7a3a', '#56a35a');
      }
    } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#5aa04e', '#2e6a31');
      const k = W / 1024;
      x.fillStyle = '#f4f1ea';
      for (let px = 0; px < W; px += 14 * k) x.fillRect(px, hz + H * 0.02, 3 * k, 14 * k);
      x.fillRect(0, hz + H * 0.025, W, 2.5 * k);
      for (let i = 0; i < 4; i++) roundTree(x, rnd() * W, hz + H * 0.06, (70 + rnd() * 40) * k, '#245f2b', '#3e8a42');
    } },
  ];
}

function desert(P) {
  const sky = P.golden ? ['#3b1f4f', '#c4486a', '#ff9a4d', '#ffd27a'] : ['#2d63b5', '#79a9dc', '#f4c98e', '#ffe2b0'];
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
      glow(x, W * 0.62, hz * 0.78, W * 0.4, '#ffe2a0', 0.75);
      x.fillStyle = '#fff4d6'; x.beginPath(); x.arc(W * 0.62, hz * 0.78, W * 0.06, 0, Math.PI * 2); x.fill();
      clouds(x, W, rnd, hz * 0.12, hz * 0.4, 3, rgba('#fff2e0', 0.75), rgba('#e7a98a', 0.6), W * 0.05);
    } },
    { depth: 1, draw(x, W, H, hz, rnd) { ridge(x, W, rnd, hz, H * 0.2, '#c98a6a', { floor: hz + 2, flat: 0.62 }); } },
    { depth: 2, draw(x, W, H, hz, rnd) { ridge(x, W, rnd, hz + 2, H * 0.12, '#a2512f', { floor: hz + 4, flat: 0.55 }); } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#d9a066', '#8a4d26');
      const k = W / 1024;
      for (let i = 0; i < 5; i++) {
        const px = rnd() * W, h = (60 + rnd() * 70) * k, b = hz + H * (0.03 + rnd() * 0.05);
        x.fillStyle = '#3f5a2c';
        x.beginPath(); x.roundRect(px - h * 0.07, b - h, h * 0.14, h, h * 0.07); x.fill();
        x.beginPath(); x.roundRect(px - h * 0.32, b - h * 0.62, h * 0.1, h * 0.36, h * 0.05); x.fill();
        x.fillRect(px - h * 0.32, b - h * 0.32, h * 0.3, h * 0.08);
        x.beginPath(); x.roundRect(px + h * 0.22, b - h * 0.74, h * 0.1, h * 0.4, h * 0.05); x.fill();
        x.fillRect(px + h * 0.02, b - h * 0.4, h * 0.3, h * 0.08);
      }
    } },
  ];
}

function track(P) {
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, [[0, '#2a0f3a'], [0.45, '#8a1f4a'], [0.8, '#ff6a3d'], [1, '#ffc27a']]); x.fillRect(0, 0, W, H);
      glow(x, W * 0.5, hz, W * 0.5, '#ffb070', 0.7);
      stars(x, W, hz * 0.35, rnd, 60);
    } },
    { depth: 1, draw(x, W, H, hz, rnd) { ridge(x, W, rnd, hz, H * 0.1, '#5a1f45', { floor: hz + 2 }); } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      const k = W / 1024;
      // grandstand + floodlight towers
      x.fillStyle = '#2a0e2c';
      x.beginPath(); x.moveTo(W * 0.05, hz); x.lineTo(W * 0.08, hz - H * 0.07); x.lineTo(W * 0.62, hz - H * 0.09); x.lineTo(W * 0.64, hz); x.fill();
      x.fillRect(W * 0.06, hz - H * 0.1, W * 0.58, 5 * k);
      for (let i = 0; i < 70; i++) { x.fillStyle = rgba(['#ffd6a8', '#ff7aa2', '#ffffff'][i % 3], 0.5); x.fillRect(W * (0.09 + rnd() * 0.52), hz - H * (0.01 + rnd() * 0.07), 2 * k, 2 * k); }
      for (const fx of [0.15, 0.42, 0.75, 0.93]) {
        x.fillStyle = '#2a0e2c'; x.fillRect(W * fx, hz - H * 0.2, 4 * k, H * 0.2);
        x.fillRect(W * fx - 12 * k, hz - H * 0.2 - 8 * k, 28 * k, 9 * k);
        glow(x, W * fx + 2 * k, hz - H * 0.2 - 4 * k, 60 * k, '#fff2d0', 0.75);
      }
    } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#3a2a3a', '#140c16');
      const k = W / 1024;
      // kerb stripes + speed streaks
      for (let px = 0; px < W; px += 36 * k) { x.fillStyle = (px / (36 * k)) % 2 < 1 ? '#e8384f' : '#f3f3f3'; x.fillRect(px, hz + H * 0.015, 36 * k, 7 * k); }
      x.save(); x.globalCompositeOperation = 'lighter';
      for (let i = 0; i < 26; i++) { const y = rnd() * hz, w = (80 + rnd() * 260) * k; x.fillStyle = rgba(['#ffd0a0', '#ff8fb0'][i % 2], 0.08 + rnd() * 0.12); x.fillRect(rnd() * W, y, w, (1 + rnd() * 2) * k); }
      x.restore();
    } },
  ];
}

function harbour(P) {
  const sky = P.golden ? ['#5a6c94', '#c9a6a0', '#ffd3a0'] : ['#7d93b0', '#b8c6d6', '#e8ecef'];
  const cols = ['#c0553f', '#3f6f9a', '#d9a23e', '#4f8a6a', '#8a4f7a', '#d0d4d8'];
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
      glow(x, W * 0.25, hz * 0.55, W * 0.3, '#fff6e6', 0.7);
      clouds(x, W, rnd, hz * 0.2, hz * 0.6, 5, rgba('#ffffff', 0.55), rgba('#aab6c4', 0.45), W * 0.09);
    } },
    { depth: 1, draw(x, W, H, hz, rnd) {
      const k = W / 1024;
      // ship hull + stacked containers on the far quay
      x.fillStyle = '#6c7a8c'; x.fillRect(W * 0.52, hz - H * 0.07, W * 0.4, H * 0.05);
      x.fillRect(W * 0.86, hz - H * 0.13, W * 0.05, H * 0.07);
      for (let cx = W * 0.54; cx < W * 0.84; cx += 22 * k) for (let r = 0; r < 2 + Math.floor(rnd() * 3); r++) { x.fillStyle = mix(cols[Math.floor(rnd() * cols.length)], '#b8c6d6', 0.45); x.fillRect(cx, hz - H * 0.07 - (r + 1) * 9 * k, 21 * k, 8.5 * k); }
    } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      const k = W / 1024;
      for (const cx of [0.12, 0.38]) {
        const px = W * cx, h = H * 0.3, c = '#2f3d52';
        x.fillStyle = c;
        x.fillRect(px, hz - h, 6 * k, h); x.fillRect(px + 60 * k, hz - h, 6 * k, h);
        x.fillRect(px - 70 * k, hz - h - 4 * k, 220 * k, 9 * k);
        x.fillRect(px + 2 * k, hz - h * 0.45, 62 * k, 5 * k);
        x.strokeStyle = c; x.lineWidth = 2 * k;
        x.beginPath(); x.moveTo(px + 33 * k, hz - h - 50 * k); x.lineTo(px - 70 * k, hz - h); x.moveTo(px + 33 * k, hz - h - 50 * k); x.lineTo(px + 150 * k, hz - h); x.stroke();
        x.fillRect(px + 30 * k, hz - h - 50 * k, 6 * k, 50 * k);
      }
      for (let cx = 0; cx < W * 0.5; cx += 26 * k) for (let r = 0; r < 1 + Math.floor(rnd() * 3); r++) { x.fillStyle = cols[Math.floor(rnd() * cols.length)]; x.fillRect(cx, hz - (r + 1) * 11 * k, 25 * k, 10.5 * k); }
    } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#5d7088', '#27313f');
      const k = W / 1024;
      for (let i = 0; i < 60; i++) { x.fillStyle = rgba('#e8f0f8', 0.08 + rnd() * 0.18); x.fillRect(rnd() * W, hz + rnd() * (H - hz) * 0.5, (20 + rnd() * 60) * k, 1.5 * k); }
    } },
  ];
}

function mountains(P) {
  const sky = P.golden ? ['#30407a', '#b06a8a', '#ffc58a'] : ['#2a6fb0', '#6fb1d8', '#d8eef2'];
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
      glow(x, W * 0.3, hz * 0.4, W * 0.28, '#fffbe8', 0.8);
      clouds(x, W, rnd, hz * 0.1, hz * 0.35, 4, rgba('#ffffff', 0.9), rgba('#cfe0ea', 0.8), W * 0.06);
      const k = W / 1024;
      x.strokeStyle = 'rgba(30,40,60,.6)'; x.lineWidth = 2 * k;
      for (let i = 0; i < 5; i++) { const bx = W * (0.55 + rnd() * 0.3), by = hz * (0.25 + rnd() * 0.2), s = (6 + rnd() * 5) * k; x.beginPath(); x.moveTo(bx - s, by); x.quadraticCurveTo(bx - s / 2, by - s * 0.6, bx, by); x.quadraticCurveTo(bx + s / 2, by - s * 0.6, bx + s, by); x.stroke(); }
    } },
    { depth: 1, draw(x, W, H, hz, rnd) { ridge(x, W, rnd, hz - H * 0.02, H * 0.36, '#8aa2c4', { floor: hz + 2, sharp: 1.6, snow: '#f2f6fa' }); } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      ridge(x, W, rnd, hz, H * 0.18, '#4f7091', { floor: hz + 2, sharp: 1.3, snow: '#dfe9f2' });
      const k = W / 1024;
      x.fillStyle = 'rgba(255,255,255,.18)'; x.fillRect(0, hz - H * 0.03, W, H * 0.03);
      for (let px = 0; px < W; px += (7 + rnd() * 9) * k) pine(x, px, hz + 2, (24 + rnd() * 26) * k, '#2c5446');
    } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#5e7a4a', '#2c3d26');
      const k = W / 1024;
      for (let i = 0; i < 14; i++) pine(x, rnd() * W, hz + H * (0.03 + rnd() * 0.05), (70 + rnd() * 60) * k, '#1d3b2e');
    } },
  ];
}

function storm(P) {
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, [[0, '#0f1230'], [0.5, '#2a2c5c'], [1, '#6a5a7a']]); x.fillRect(0, 0, W, H);
      clouds(x, W, rnd, hz * 0.05, hz * 0.45, 9, rgba('#4a4c7c', 0.95), rgba('#1b1c3a', 0.95), W * 0.1);
      const k = W / 1024;
      glow(x, W * 0.62, hz * 0.3, W * 0.35, '#ffe46a', 0.35);
      bolt(x, rnd, W * 0.62, hz * 0.25, hz * 0.95, '#ffe14d', k);
      bolt(x, rnd, W * 0.2, hz * 0.3, hz * 0.8, '#ffe14d', k * 0.7);
    } },
    { depth: 1, draw(x, W, H, hz) { hills(x, W, hz, H * 0.08, 1.3, 1.7, '#3a3a5e', hz + 2); } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      const k = W / 1024;
      hills(x, W, hz + 2, H * 0.04, 2.2, 0.5, '#242446', hz + 4);
      for (let i = 0; i < 6; i++) turbine(x, W * (0.08 + i * 0.17 + rnd() * 0.05), hz - H * 0.02, (60 + rnd() * 40) * k, '#c9c9e0', rnd() * 3, k);
      const pts = [];
      for (let i = 0; i < 4; i++) { const px = W * (0.05 + i * 0.32), h = H * 0.16; pylon(x, px, hz + 2, h, '#151530', k); pts.push([px, hz + 2 - h * 0.78]); }
      wires(x, pts, '#151530', k, 18 * k);
    } },
    { depth: 3, draw(x, W, H, hz) { ground(x, W, H, hz, '#2c2a48', '#0f0e1e'); } },
  ];
}

function site(P) {
  const sky = P.golden ? ['#3b3f63', '#c27a5a', '#ffcf8a'] : ['#5d7590', '#b2a99a', '#f0d6a8'];
  return [
    { depth: 0, draw(x, W, H, hz, rnd) {
      x.fillStyle = vgrad(x, 0, hz, sky.map((c, i) => [i / (sky.length - 1), c])); x.fillRect(0, 0, W, H);
      glow(x, W * 0.7, hz * 0.7, W * 0.35, '#fff0c8', 0.75);
      clouds(x, W, rnd, hz * 0.15, hz * 0.5, 4, rgba('#fff6ea', 0.6), rgba('#b9a594', 0.5), W * 0.08);
    } },
    { depth: 1, draw(x, W, H, hz, rnd) { skyline(x, W, rnd, hz, H * 0.06, H * 0.2, '#8e94a0', null, { wmin: 0.04, wmax: 0.09 }); } },
    { depth: 2, draw(x, W, H, hz, rnd) {
      const k = W / 1024, c = '#3b3f4c';
      // building skeleton
      const bx = W * 0.5, bw = W * 0.34, bh = H * 0.26;
      x.strokeStyle = c; x.lineWidth = 3 * k;
      for (let i = 0; i <= 6; i++) { x.beginPath(); x.moveTo(bx + (bw * i) / 6, hz); x.lineTo(bx + (bw * i) / 6, hz - bh * (i < 5 ? 1 : 0.7)); x.stroke(); }
      for (let j = 1; j <= 7; j++) { const y = hz - (bh * j) / 7; x.beginPath(); x.moveTo(bx, y); x.lineTo(bx + bw * (j > 5 ? 0.8 : 1), y); x.stroke(); }
      x.fillStyle = 'rgba(220,120,40,.5)'; x.fillRect(bx + bw * 0.1, hz - bh * 0.42, bw * 0.3, bh * 0.14);
      crane(x, W * 0.3, hz, H * 0.36, W * 0.3, '#e0a22a', k);
      crane(x, W * 0.86, hz, H * 0.3, W * 0.2, '#d08a1e', k);
    } },
    { depth: 3, draw(x, W, H, hz, rnd) {
      ground(x, W, H, hz, '#9a7a5a', '#4a3828');
      const k = W / 1024;
      x.fillStyle = '#7a5a3e';
      for (let i = 0; i < 3; i++) { const px = rnd() * W, w = (120 + rnd() * 100) * k; x.beginPath(); x.ellipse(px, hz + H * 0.03, w, w * 0.25, 0, Math.PI, 0); x.fill(); }
      for (let px = 0; px < W; px += 50 * k) { x.fillStyle = '#e8862a'; x.beginPath(); x.moveTo(px, hz + H * 0.05); x.lineTo(px + 6 * k, hz + H * 0.05 - 18 * k); x.lineTo(px + 12 * k, hz + H * 0.05); x.fill(); }
    } },
  ];
}

// ------------------------------------------------------------------ painting

// finish -> mood: standard cards get the theme's daylight palette, full art a dramatic grade with rays,
// SIR golden hour with rays and bokeh.
function moodOf(finish) {
  return { golden: finish === 'sir', drama: finish === 'fullart' || finish === 'sir', dull: finish === 'none' };
}

function finishTouches(x, W, H, hz, P, rnd) {
  const k = W / 1024;
  if (P.drama) rays(x, W * 0.5, hz * 0.7, W, H, P.golden ? '#ffe2a0' : '#ffffff', P.golden ? 0.09 : 0.055, 12, rnd);
  if (P.golden) bokeh(x, W, H, rnd, 26, ['#ffd27a', '#ff9ec4', '#fff2c8'], k);
  if (P.drama) { // grade: deepen the edges so the car pops
    const v = x.createRadialGradient(W / 2, H * 0.5, W * 0.25, W / 2, H * 0.5, W * 0.85);
    v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, P.golden ? 'rgba(60,20,40,.45)' : 'rgba(10,10,30,.4)');
    x.fillStyle = v; x.fillRect(0, 0, W, H);
  }
}

let grainCanvas = null;
function grain(x, W, H, a) {
  if (!grainCanvas) {
    grainCanvas = document.createElement('canvas');
    grainCanvas.width = grainCanvas.height = 128;
    const g = grainCanvas.getContext('2d'), d = g.createImageData(128, 128);
    for (let i = 0; i < d.data.length; i += 4) { const v = Math.random() * 255; d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; }
    g.putImageData(d, 0, 0);
  }
  x.save(); x.globalAlpha = a; x.globalCompositeOperation = 'overlay';
  x.fillStyle = x.createPattern(grainCanvas, 'repeat'); x.fillRect(0, 0, W, H);
  x.restore();
}

function themeLayers(typeKey, finish) {
  const P = moodOf(finish);
  return { P, layers: (THEMES[typeKey] || THEMES.commuter)(P) };
}

const cache = new Map();
function remember(key, make) {
  if (!cache.has(key)) {
    if (cache.size > 48) cache.delete(cache.keys().next().value);
    cache.set(key, make());
  }
  return cache.get(key);
}

// One canvas for a card's art window backdrop. horizon: fraction of the height where the land starts.
export function paintBackdrop(typeKey, finish, variant = 0, { w = 768, h = 960, horizon = 0.6 } = {}) {
  return remember(`card|${typeKey}|${finish}|${variant}|${w}x${h}`, () => {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const x = c.getContext('2d');
    const { P, layers } = themeLayers(typeKey, finish);
    const rnd = rng(7919 * (variant + 1) + typeKey.length * 131);
    const hz = h * horizon;
    for (const L of layers) L.draw(x, w, h, hz, rnd, P);
    finishTouches(x, w, h, hz, P, rnd);
    grain(x, w, h, 0.08);
    return c;
  });
}

// Inspector background: the same scene split into depth groups (sky, far, mid, near), each softened by a blur that
// grows with distance from the card. Returns [{depth, canvas}] (all but the sky have transparent gaps).
export function paintLayers(typeKey, finish, { w = 2048, h = 1152, horizon = 0.66 } = {}) {
  return remember(`insp|${typeKey}|${finish}|${w}x${h}`, () => {
    const { P, layers } = themeLayers(typeKey, finish);
    const rnd = rng(104729 + typeKey.length * 31);
    const hz = h * horizon;
    const blur = [7, 4.5, 3, 2];
    return layers.map((L) => {
      const raw = document.createElement('canvas');
      raw.width = w; raw.height = h;
      const x = raw.getContext('2d');
      L.draw(x, w, h, hz, rnd, P);
      if (L.depth === 0) finishTouches(x, w, h, hz, P, rnd);
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const y = c.getContext('2d');
      y.filter = `blur(${blur[L.depth] * (w / 2048)}px)`;
      y.drawImage(raw, 0, 0);
      y.filter = 'none';
      if (L.depth === 0) grain(y, w, h, 0.06);
      return { depth: L.depth, canvas: c };
    });
  });
}
