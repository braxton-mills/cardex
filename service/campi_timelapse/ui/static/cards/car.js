// Procedural PBR cars for the Cards tab, plus loading the TripoSR meshes the background job makes.
// A style is a side silhouette (rear x=0 -> front x=L, metres, y up) with wheel arches cut out of the bottom edge, a
// greenhouse quad [rear base, rear top, front top, front base], and a few extras. The body is lofted from
// cross-sections along that profile (rounded plan, shoulder, tumblehome, wheel flares) with glass, pillars, seals and
// shut lines painted on by region, then dressed with lights, grille, plates, mirrors, handles and alloy wheels,
// merged into one mesh per material, and normalised to length 1 / ground at y=0 / centred.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const S = {
  sedan: { L: 4.85, W: 1.85, wr: 0.34, axles: [0.98, 3.82], sill: 0.3, bl: 0.95,
    body: [[0.03, 0.34], [0, 0.56], [0.06, 0.86], [0.85, 0.95], [1.1, 0.96], [3.3, 0.93], [4.55, 0.8], [4.84, 0.64], [4.84, 0.4], [4.74, 0.3]],
    gh: [[1.08, 0.96], [1.72, 1.43], [2.92, 1.45], [3.32, 0.93]] },
  coupe: { L: 4.6, W: 1.9, wr: 0.35, axles: [0.95, 3.6], sill: 0.27, bl: 0.9, caliper: true,
    body: [[0.03, 0.32], [0, 0.55], [0.08, 0.88], [0.8, 0.92], [1.05, 0.93], [3.1, 0.88], [4.35, 0.72], [4.6, 0.56], [4.58, 0.36], [4.48, 0.27]],
    gh: [[1.0, 0.93], [1.85, 1.3], [2.6, 1.31], [3.12, 0.88]] },
  hatch: { L: 4.3, W: 1.8, wr: 0.33, axles: [0.72, 3.35], sill: 0.3, bl: 0.98,
    body: [[0.03, 0.34], [0, 0.6], [0.02, 0.95], [0.12, 1.0], [2.85, 0.96], [4.0, 0.83], [4.3, 0.64], [4.29, 0.4], [4.2, 0.3]],
    gh: [[0.14, 1.0], [0.42, 1.46], [2.5, 1.49], [2.88, 0.96]] },
  suv: { L: 4.65, W: 1.86, wr: 0.37, axles: [0.9, 3.6], sill: 0.42, bl: 1.08, cladding: true,
    body: [[0.03, 0.46], [0, 0.72], [0.02, 1.05], [0.22, 1.1], [3.0, 1.08], [4.32, 0.98], [4.63, 0.82], [4.62, 0.52], [4.52, 0.42]],
    gh: [[0.24, 1.1], [0.42, 1.66], [2.72, 1.7], [3.12, 1.08]] },
  bigsuv: { L: 5.25, W: 2.02, wr: 0.41, axles: [1.0, 4.05], sill: 0.48, bl: 1.2, cladding: true,
    body: [[0.03, 0.52], [0, 0.8], [0.02, 1.16], [0.15, 1.2], [3.6, 1.2], [4.85, 1.13], [5.24, 0.96], [5.23, 0.6], [5.12, 0.48]],
    gh: [[0.17, 1.2], [0.27, 1.88], [3.32, 1.92], [3.68, 1.2]] },
  offroad: { L: 4.45, W: 1.9, wr: 0.43, axles: [0.8, 3.75], sill: 0.55, bl: 1.2, cladding: true, spare: true,
    body: [[0.02, 0.6], [0, 1.18], [0.06, 1.22], [3.4, 1.22], [4.35, 1.17], [4.45, 1.05], [4.44, 0.66], [4.36, 0.55]],
    gh: [[0.06, 1.22], [0.08, 1.86], [3.05, 1.88], [3.38, 1.22]] },
  pickup: { L: 5.9, W: 2.02, wr: 0.41, axles: [0.95, 4.65], sill: 0.5, bl: 1.24, bed: [0.08, 2.3], cladding: true,
    body: [[0.03, 0.55], [0, 0.82], [0, 1.22], [2.3, 1.22], [2.36, 1.24], [4.42, 1.22], [5.7, 1.14], [5.9, 0.98], [5.89, 0.6], [5.8, 0.5]],
    gh: [[2.42, 1.24], [2.48, 1.9], [3.98, 1.95], [4.46, 1.22]] },
  hdpickup: { L: 6.6, W: 2.08, wr: 0.44, axles: [1.05, 5.15], sill: 0.6, bl: 1.36, bed: [0.08, 2.5], cladding: true,
    body: [[0.03, 0.65], [0, 0.92], [0, 1.34], [2.5, 1.34], [2.56, 1.36], [4.95, 1.34], [6.35, 1.3], [6.6, 1.12], [6.59, 0.7], [6.5, 0.6]],
    gh: [[2.62, 1.36], [2.68, 2.02], [4.5, 2.06], [4.98, 1.34]] },
  minivan: { L: 5.15, W: 1.99, wr: 0.36, axles: [0.98, 4.05], sill: 0.36, bl: 1.05,
    body: [[0.03, 0.4], [0, 0.7], [0.03, 1.02], [0.2, 1.06], [3.75, 1.04], [4.85, 0.9], [5.15, 0.7], [5.14, 0.45], [5.04, 0.36]],
    gh: [[0.22, 1.06], [0.42, 1.74], [3.55, 1.78], [4.12, 1.04]] },
  van: { L: 5.95, W: 2.05, wr: 0.38, axles: [1.1, 4.7], sill: 0.42, bl: 1.15, tall: 2.55,
    body: [[0.03, 0.46], [0, 2.45], [0.12, 2.55], [4.35, 2.55], [4.95, 1.95], [5.05, 1.2], [5.75, 1.02], [5.95, 0.8], [5.94, 0.52], [5.85, 0.42]],
    gh: [[4.3, 1.18], [4.32, 2.47], [4.36, 2.5], [4.98, 1.18]], vanGlass: true, ws: [4.4, 4.92] },
  boxtruck: { L: 7.2, W: 2.3, wr: 0.48, axles: [1.6, 5.6], sill: 0.6, bl: 1.45, tall: 3.3, box: [0, 5.15],
    body: [[0.02, 0.7], [0, 3.25], [0.08, 3.3], [5.15, 3.3], [5.15, 2.5], [6.75, 2.45], [6.95, 1.7], [7.2, 1.4], [7.19, 0.75], [7.1, 0.6]],
    gh: [[5.4, 1.5], [5.42, 2.4], [6.7, 2.38], [6.92, 1.5]], vanGlass: true, ws: [6.77, 6.93] },
  bus: { L: 11.5, W: 2.5, wr: 0.5, axles: [2.6, 9.5], sill: 0.45, bl: 1.55, tall: 3.15,
    body: [[0.02, 0.55], [0, 3.05], [0.15, 3.15], [11.3, 3.15], [11.5, 2.95], [11.5, 0.6], [11.4, 0.45]],
    gh: [[10.7, 1.4], [10.75, 2.85], [10.8, 2.88], [11.47, 1.4]], vanGlass: true, windows: [0.5, 10.4, 1.65, 2.75, 9] },
};

let flakeTex = null;
function flakes() {
  if (flakeTex) return flakeTex;
  const n = 256, d = new Uint8Array(n * n * 4);
  for (let i = 0; i < n * n; i++) {
    const x = Math.random() * 2 - 1, y = Math.random() * 2 - 1;
    d[i * 4] = 128 + x * 70; d[i * 4 + 1] = 128 + y * 70; d[i * 4 + 2] = 255; d[i * 4 + 3] = 255;
  }
  flakeTex = new THREE.DataTexture(d, n, n);
  flakeTex.wrapS = flakeTex.wrapT = THREE.RepeatWrapping;
  flakeTex.repeat.set(10, 10);
  flakeTex.needsUpdate = true;
  return flakeTex;
}

// Shared materials keyed by role (+ stencil masking + paint colour); the per-card engine sets stencil on them.
const matCache = new Map();
export function material(role, { color = '#888', stencil = false, sir = false } = {}) {
  const key = `${role}|${stencil}|${role === 'paint' ? `${color}|${sir}` : ''}`;
  if (matCache.has(key)) return matCache.get(key);
  let m;
  switch (role) {
    case 'paint': {
      const c = new THREE.Color(color);
      const hsl = c.getHSL({});
      m = new THREE.MeshPhysicalMaterial({
        color: c, metalness: hsl.s < 0.12 && hsl.l > 0.85 ? 0.05 : hsl.s < 0.12 && hsl.l > 0.5 ? 0.75 : 0.5,
        roughness: hsl.l > 0.85 ? 0.22 : 0.32, clearcoat: 1,
        clearcoatRoughness: 0.035, normalMap: flakes(), normalScale: new THREE.Vector2(0.04, 0.04), envMapIntensity: 1.25,
      });
      if (sir) Object.assign(m, { iridescence: 0.55, iridescenceIOR: 1.7, iridescenceThicknessRange: [180, 620] });
      break;
    }
    case 'glass': m = new THREE.MeshPhysicalMaterial({ color: '#05070b', metalness: 0.1, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.8 }); break;
    case 'chrome': m = new THREE.MeshStandardMaterial({ color: '#d9dde3', metalness: 1, roughness: 0.14, envMapIntensity: 1.4 }); break;
    case 'rim': m = new THREE.MeshStandardMaterial({ color: '#5c626b', metalness: 1, roughness: 0.32, side: THREE.DoubleSide }); break;
    case 'alloy': m = new THREE.MeshStandardMaterial({ color: '#d4d8de', metalness: 1, roughness: 0.2, envMapIntensity: 1.3 }); break;
    case 'plate': m = new THREE.MeshStandardMaterial({ color: '#eef1f5', roughness: 0.4, emissive: '#ffffff', emissiveIntensity: 0.08 }); break;
    case 'amber': m = new THREE.MeshStandardMaterial({ color: '#5a2a00', emissive: '#ff8a1a', emissiveIntensity: 2.2, roughness: 0.3 }); break;
    case 'tire': m = new THREE.MeshStandardMaterial({ color: '#141416', metalness: 0, roughness: 0.86 }); break;
    case 'trim': m = new THREE.MeshStandardMaterial({ color: '#16181c', metalness: 0.2, roughness: 0.55 }); break;
    case 'liner': m = new THREE.MeshStandardMaterial({ color: '#050506', roughness: 1, side: THREE.DoubleSide }); break;
    case 'head': m = new THREE.MeshStandardMaterial({ color: '#ffffff', emissive: '#fff4dc', emissiveIntensity: 5, roughness: 0.2 }); break;
    case 'tail': m = new THREE.MeshStandardMaterial({ color: '#400004', emissive: '#ff1428', emissiveIntensity: 3.2, roughness: 0.3 }); break;
    case 'caliper': m = new THREE.MeshStandardMaterial({ color: '#d0201a', metalness: 0.3, roughness: 0.4 }); break;
    case 'silhouette': m = new THREE.MeshStandardMaterial({ color: '#06070b', metalness: 0.6, roughness: 0.35, envMapIntensity: 0.25 }); break;
    default: m = new THREE.MeshStandardMaterial({ color: '#888' });
  }
  if (stencil) Object.assign(m, { stencilWrite: true, stencilRef: 1, stencilFunc: THREE.EqualStencilFunc });
  matCache.set(key, m);
  return m;
}

const clamp = THREE.MathUtils.clamp;
const lerp = THREE.MathUtils.lerp;
const sm = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };

// x extent of a closed polygon on the horizontal line y
function extentAt(poly, y) {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i], [x2, y2] = poly[(i + 1) % poly.length];
    if ((y1 - y) * (y2 - y) > 0 || y1 === y2) continue;
    const x = x1 + ((y - y1) / (y2 - y1)) * (x2 - x1);
    lo = Math.min(lo, x); hi = Math.max(hi, x);
  }
  return [lo, hi];
}

// y extent of a closed polygon on the vertical line x (profiles are x-monotone: one interval per x)
function spanAt(poly, x) {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i], [x2, y2] = poly[(i + 1) % poly.length];
    if ((x1 - x) * (x2 - x) > 0 || x1 === x2) continue;
    const y = y1 + ((x - x1) / (x2 - x1)) * (y2 - y1);
    lo = Math.min(lo, y); hi = Math.max(hi, y);
  }
  return [lo, hi];
}

function shrink(pts, inset) {
  const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length, cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  return pts.map(([x, y]) => {
    const dx = cx - x, dy = cy - y, l = Math.hypot(dx, dy) || 1;
    return [x + (dx / l) * inset * 1.4, y + (dy / l) * inset];
  });
}

// ---- body: a loft of cross-sections along the car.
// Each section (at x) runs from the sill (or the wheel arch) up the side to the beltline, up the greenhouse with
// tumblehome to the roof edge, and over a crowned roof (or hood/deck where there is no greenhouse) to the centre;
// mirrored, closed under the floor. Plan view rounds the corners, the sides bulge at the shoulder and flare over the
// wheels. Materials are painted on by region: glass, pillars, window seals, door and hood shut lines, cladding,
// sunroof, bed liner. Lights, grille, plates, mirrors, handles etc. are separate parts placed on the surface.

const T_ROWS = [0.05, 0.1, 0.3, 0.5, 0.7, 0.86, 0.92, 1];                  // greenhouse rows (0 = belt, 1 = roof edge)
const V_ROWS = [0.04, 0.1, 0.12, 0.135, 0.2, 0.32, 0.45, 0.62, 0.8, 1];    // roof rows (0 = edge, 1 = centre)

function bodyFrame(style, st, hq) {
  const tall = !!st.tall;
  const xs = st.body.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const hw = st.W / 2, R = st.wr * 1.2;
  const gh = st.vanGlass ? null : st.gh;
  const plan = (x) => {
    const d = Math.min(x - x0, x1 - x), rc = tall ? 0.2 : 0.45, u = Math.max(0, 1 - d / rc);
    return hw * (1 - (tall ? 0.05 : 0.15) * u * u);
  };
  const s01 = (y) => (y - st.sill) / (st.bl - st.sill);
  const side = (y) => { const s = s01(y); return 1 - (tall ? 0.03 : 0.08) * (1 - sm(-0.45, 0.5, s)) ** 2 - (tall ? 0.01 : 0.03) * sm(0.62, 1, s) ** 2; };
  const flareAmt = tall ? 0.012 : st.cladding ? 0.04 : 0.028;
  const flare = (x, y) => {
    let f = 0;
    for (const ax of st.axles) f = Math.max(f, Math.exp(-(((x - ax) / (R * 1.3)) ** 4)));
    const s = s01(y);
    return flareAmt * f * sm(-0.1, 0.3, s) * (1 - sm(0.55, 0.95, s));
  };
  const halfZ = (x, y) => plan(x) * side(y) + flare(x, y);
  const archY = (x) => {
    let y = -Infinity;
    for (const ax of st.axles) { const dx = x - ax; if (Math.abs(dx) < R) y = Math.max(y, st.wr + Math.sqrt(R * R - dx * dx)); }
    return y;
  };
  const span = (x) => {
    const [lo, hi] = spanAt(st.body, x);
    let top = hi;
    if (gh && x >= gh[0][0] && x <= gh[3][0]) { const [, gt] = spanAt(gh, x); if (gt > top) top = gt; }
    return { yb: Math.min(Math.max(lo, archY(x)), hi), belt: hi, top };
  };
  const tanT = tall ? 0.04 : 0.3;
  const crown = (ghH) => (tall ? 0.03 : 0.025 + 0.04 * Math.min(1, ghH / 0.5));
  const roll = tall ? 0.01 : 0.02;
  // a point on the surface: band 'gh' (param t) or 'top' (param v), on the +z side -> [z, y]
  const at = (x, band, p) => {
    const { belt, top } = span(x), ghH = top - belt, zb = halfZ(x, belt);
    if (band === 'gh') return [zb - ghH * tanT * p ** 1.2, belt + p * ghH];
    const zE = zb - ghH * tanT;
    return [zE * (1 - p), top + crown(ghH) * (1 - (1 - p) ** 2) + roll * sm(0, 0.16, p)];
  };

  // ---- regions
  const keys = [];
  const rng = (a, b) => { keys.push(a, b); return [a, b]; };
  const fa = Math.max(...st.axles), ra = Math.min(...st.axles);
  let win = null, ws = null, rw = null, sun = null, hoodSeam = null, trunkSeam = null;
  const pillars = [], seams = [];
  if (gh) {
    const [rb, rt, ft, fb] = gh;
    const w0 = rb[0] + Math.max(0.08, (rt[0] - rb[0]) * 0.55);
    win = rng(w0, fb[0] - 0.03);
    const mid = (rt[0] + ft[0]) / 2 + (fb[0] - rb[0]) * 0.04;
    const two = style === 'coupe';
    if (!two) { pillars.push([mid, 0.05, 'trim']); keys.push(mid - 0.05, mid + 0.05); }
    seams.push(Math.min(fb[0] + 0.06, fa - R - 0.04));
    if (!two) seams.push(mid);
    let rearEnd = Math.max(w0 + 0.04, ra + R + 0.06);
    if (fb[0] - rb[0] > 2.4 && !st.bed) {
      const c = Math.max(ra + R * 0.8, w0 + 0.35);
      pillars.push([c, 0.035, 'paint']); keys.push(c - 0.035, c + 0.035);
      rearEnd = c;
    }
    seams.push(rearEnd);
    ws = rng(ft[0] + 0.03, fb[0] - 0.05);
    rw = st.bed ? rng(lerp(rb[0], rt[0], 0.3), lerp(rb[0], rt[0], 0.85)) : rng(rb[0] + 0.04, rt[0] - 0.03);
    if (st.bed) for (let i = 0; i <= 10; i++) keys.push(lerp(rb[0], rt[0], i / 10)); // the cab's back wall needs rows
    if (['sedan', 'suv', 'hatch', 'bigsuv'].includes(style)) sun = rng(ft[0] - 0.9, ft[0] - 0.15);
    hoodSeam = fb[0] + 0.1;
    if (style === 'sedan' || style === 'coupe') trunkSeam = rb[0] - 0.05;
    if (st.bed) seams.push(st.bed[1] + 0.06);
  } else {
    ws = st.ws ? rng(...st.ws) : null;
    if (!st.windows) { seams.push(st.gh[0][0] - 0.04, st.gh[3][0] + 0.02); if (style === 'van') seams.push(2.4); }
    if (st.box) seams.push(st.box[1]);
  }
  for (const s of seams) keys.push(s - 0.0065, s + 0.0065);
  if (hoodSeam) keys.push(hoodSeam - 0.0065, hoodSeam + 0.0065);
  if (trunkSeam) keys.push(trunkSeam - 0.0065, trunkSeam + 0.0065);
  if (st.bed) keys.push(st.bed[0] + 0.05, st.bed[1] - 0.03);
  const seal = ['sedan', 'coupe', 'bigsuv'].includes(style) ? 'chrome' : 'trim';
  const near = (x, s, w = 0.0065) => s != null && Math.abs(x - s) < w;

  const classify = (band, x, y, p, sec) => {
    if (band === 'top') {
      const v = p;
      if (st.bed && x > st.bed[0] + 0.05 && x < st.bed[1] - 0.03) return v > 0.1 ? 'liner' : 'paint';
      if (ws && x > ws[0] && x < ws[1]) return v > 0.11 ? 'glass' : 'paint';
      if (rw && x > rw[0] && x < rw[1]) return v > (st.bed ? 0.3 : 0.11) ? 'glass' : 'paint';
      if (sun && x > sun[0] && x < sun[1] && v > 0.32) return 'glass';
      if (hoodSeam && ((near(x, hoodSeam) && v > 0.12) || (x > hoodSeam && x < x1 - 0.22 && v > 0.12 && v < 0.135))) return 'liner';
      if (trunkSeam && ((near(x, trunkSeam) && v > 0.12) || (x < trunkSeam && x > x0 + 0.2 && v > 0.12 && v < 0.135))) return 'liner';
      return 'paint';
    }
    if (band === 'gh') {
      const t = p;
      if (!win || x < win[0] || x > win[1] || t > 0.92) return 'paint';
      if (t < 0.05) return seal;
      for (const [px, pw, role] of pillars) if (Math.abs(x - px) < pw) return role;
      return 'glass';
    }
    for (const s of seams) if (near(x, s) && y > sec.yb + 0.02 && y < sec.belt - 0.012) return 'liner';
    if (st.windows) {
      const [wx0, wx1, wy0, wy1, n] = st.windows, w = (wx1 - wx0) / n;
      if (x > wx0 && x < wx1 && y > wy0 && y < wy1) return ((x - wx0) % w) > 0.08 && ((x - wx0) % w) < w - 0.08 ? 'glass' : 'trim';
    }
    if (st.cladding && y < st.sill + 0.14) return 'trim'; // arch cladding is a separate lip (smooth, not stepped)
    if ((x > x1 - 0.3 || x < x0 + 0.3) && y < st.sill + 0.11) return 'trim';
    return 'paint';
  };

  // absolute height levels for the lower band (so window and cladding edges stay level across the arches)
  const top = Math.max(...st.body.map((p) => p[1]), ...st.gh.map((p) => p[1]));
  const lv = [];
  for (let y = 0; y < top; y += hq ? 0.05 : 0.1) lv.push(y);
  lv.push(st.sill + 0.11, st.sill + 0.14, st.bl - 0.012);
  if (st.windows) lv.push(st.windows[2], st.windows[3]);
  const levels = [...new Set(lv.map((y) => Math.round(y * 1000) / 1000))].sort((a, b) => a - b);

  return { x0, x1, R, plan, halfZ, span, at, tanT, crown, roll, classify, keys, levels, ws, seams, top, tall };
}

function loftBody(st, F, hq) {
  const { x0, x1 } = F;
  const raw = [];
  const step = hq ? 0.04 : 0.07, fine = hq ? 0.014 : 0.025;
  for (let x = x0; x < x1; x += step) raw.push(x);
  for (let d = 0; d < 0.3; d += fine) raw.push(x0 + d, x1 - d);
  for (const p of [...st.body, ...st.gh]) raw.push(p[0]);
  raw.push(...F.keys);
  for (const ax of st.axles) for (let i = 0; i <= 24; i++) raw.push(ax + F.R * Math.cos((i / 24) * Math.PI));
  const sorted = raw.filter((x) => x >= x0 && x <= x1).sort((a, b) => a - b);
  const X = [x0];
  for (const x of sorted) if (x - X[X.length - 1] > 0.0012) X.push(x);
  if (x1 - X[X.length - 1] > 1e-6) X.push(x1);

  // half profile meta (shared by every section)
  const nLow = F.levels.length + 2;
  const band = [], param = [];
  for (let i = 0; i < nLow; i++) { band.push('low'); param.push(0); }
  for (const t of T_ROWS) { band.push('gh'); param.push(t); }
  for (const v of V_ROWS) { band.push('top'); param.push(v); }
  const n = band.length, N = 2 * n - 1;
  const hidx = (j) => (j < n ? j : 2 * n - 2 - j);

  const pos = new Float32Array(X.length * N * 3);
  const secs = X.map((x, i) => {
    const sec = F.span(x);
    const { yb, belt, top } = sec;
    const half = [];
    for (const y of [yb, ...F.levels.map((y) => clamp(y, yb, belt)), belt]) half.push([F.halfZ(x, y), y]);
    const ghH = top - belt, zb = half[half.length - 1][0];
    for (const t of T_ROWS) half.push([zb - ghH * F.tanT * t ** 1.2, belt + t * ghH]);
    const zE = half[half.length - 1][0], cr = F.crown(ghH);
    for (const v of V_ROWS) half.push([zE * (1 - v), top + cr * (1 - (1 - v) ** 2) + F.roll * sm(0, 0.16, v)]);
    for (let j = 0; j < N; j++) {
      const [z, y] = half[hidx(j)];
      const o = (i * N + j) * 3;
      pos[o] = x; pos[o + 1] = y; pos[o + 2] = j < n ? z : -z;
    }
    return sec;
  });

  const index = [], roles = [];
  const P = (i, j) => i * N + j;
  const y = (k) => pos[k * 3 + 1], z = (k) => pos[k * 3 + 2];
  const tiny = (k1, k2) => Math.abs(y(k1) - y(k2)) + Math.abs(z(k1) - z(k2)) < 1e-6;
  for (let i = 0; i < X.length - 1; i++) {
    const xm = (X[i] + X[i + 1]) / 2;
    for (let j = 0; j < N; j++) {
      const j2 = (j + 1) % N;
      const a = P(i, j), b = P(i + 1, j), c = P(i + 1, j2), d = P(i, j2);
      if (tiny(a, d) && tiny(b, c)) continue;
      let role;
      if (j === N - 1) { // floor; where it wraps up a blunt end it is bodywork
        const e1 = [pos[b * 3] - pos[a * 3], y(b) - y(a), z(b) - z(a)], e2 = [0, y(d) - y(a), z(d) - z(a)];
        const ny = e1[2] * e2[0] - e1[0] * e2[2], nl = Math.hypot(e1[1] * e2[2] - e1[2] * e2[1], ny, e1[0] * e2[1] - e1[1] * e2[0]);
        role = nl && Math.abs(ny / nl) < 0.6 ? 'paint' : 'trim';
      }
      else {
        const hi = Math.max(hidx(j), hidx(j2));
        const bd = band[hi], p = band[hi - 1] === bd ? (param[hi - 1] + param[hi]) / 2 : param[hi] / 2;
        role = F.classify(bd, xm, (y(a) + y(b) + y(c) + y(d)) / 4, p, secs[i]);
      }
      index.push(a, b, c, a, c, d);
      roles.push(role, role);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setIndex(index);
  g.computeVertexNormals();
  const nor = g.attributes.normal.array;
  const by = new Map();
  const out = (role) => { if (!by.has(role)) by.set(role, { p: [], n: [] }); return by.get(role); };
  for (let t = 0; t < roles.length; t++) {
    const o = out(roles[t]);
    for (let k = 0; k < 3; k++) {
      const v = index[t * 3 + k];
      o.p.push(pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]);
      o.n.push(nor[v * 3], nor[v * 3 + 1], nor[v * 3 + 2]);
    }
  }
  // end caps (the blunt front of a nose, the flat back of a tall body)
  for (const [i, sgn] of [[0, -1], [X.length - 1, 1]]) {
    let cy = 0, cz = 0;
    for (let j = 0; j < N; j++) { cy += y(P(i, j)); cz += z(P(i, j)); }
    cy /= N; cz /= N;
    const o = out('paint');
    for (let j = 0; j < N; j++) {
      const a = P(i, j), b = P(i, (j + 1) % N);
      if (tiny(a, b)) continue;
      const [p1, p2] = sgn > 0 ? [b, a] : [a, b];
      o.p.push(X[i], cy, cz, X[i], y(p1), z(p1), X[i], y(p2), z(p2));
      o.n.push(sgn, 0, 0, sgn, 0, 0, sgn, 0, 0);
    }
  }
  g.dispose();
  const parts = [];
  for (const [role, { p, n: nn }] of by) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
    geo.setAttribute('normal', new THREE.Float32BufferAttribute(nn, 3));
    parts.push([geo, role]);
  }
  return parts;
}

// ---- parts

function rbox(w, h, d, r, role, { x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0 } = {}) {
  const g = new RoundedBoxGeometry(w, h, d, 2, Math.max(1e-4, Math.min(r, w / 2 - 1e-4, h / 2 - 1e-4, d / 2 - 1e-4)));
  if (rz) g.rotateZ(rz);
  if (ry) g.rotateY(ry);
  if (rx) g.rotateX(rx);
  g.translate(x, y, z);
  return [g, role];
}

function box(w, h, d, x, y, z, role) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(x, y, z);
  return [g, role];
}

function cyl(r, len, x, y, z, role, axis = 'x', seg = 16) {
  const g = new THREE.CylinderGeometry(r, r, len, seg);
  if (axis === 'x') g.rotateZ(Math.PI / 2); else if (axis === 'z') g.rotateX(Math.PI / 2);
  g.translate(x, y, z);
  return [g, role];
}

// Wheel at the origin, axis along z (outer face +z): grooved tyre with a rounded shoulder, twin five-spoke alloy
// with a dished face, hub, cap and lug nuts, brake disc and caliper.
function wheelParts(wr, tw, hq, caliper) {
  const parts = [];
  const seg = hq ? 64 : 28;
  const rb = wr * 0.66, h = tw / 2, sw = wr - rb, sh = Math.min(0.035, h * 0.4);
  const prof = [[rb, -h * 0.9], [rb + sw * 0.4, -h * 1.02], [rb + sw * 0.8, -h * 0.98]];
  for (let i = 0; i <= 4; i++) { const a = -Math.PI / 2 + (i / 4) * (Math.PI / 2); prof.push([wr - sh + Math.cos(a) * sh, -h + sh + Math.sin(a) * sh]); }
  const tread = h - sh;
  for (const gz of [-0.4, 0, 0.4]) {
    const c = gz * tread, gw = 0.007;
    prof.push([wr, c - gw * 1.6], [wr - 0.012, c - gw], [wr - 0.012, c + gw], [wr, c + gw * 1.6]);
  }
  for (let i = 0; i <= 4; i++) { const a = (i / 4) * (Math.PI / 2); prof.push([wr - sh + Math.cos(a) * sh, h - sh + Math.sin(a) * sh]); }
  prof.push([rb + sw * 0.8, h * 0.98], [rb + sw * 0.4, h * 1.02], [rb, h * 0.9]);
  const tire = new THREE.LatheGeometry(prof.map(([r, z]) => new THREE.Vector2(r, z)), seg);
  tire.rotateX(Math.PI / 2);
  parts.push([tire, 'tire']);
  const barrel = new THREE.CylinderGeometry(rb, rb, tw * 0.85, seg, 1, true);
  barrel.rotateX(Math.PI / 2);
  parts.push([barrel, 'rim']);
  parts.push(cyl(rb * 0.97, 0.01, 0, 0, -h * 0.1, 'trim', 'z', seg));
  const lip = new THREE.TorusGeometry(rb * 0.97, 0.013, 8, seg);
  lip.translate(0, 0, h * 0.78);
  parts.push([lip, 'alloy']);
  // spokes: tapered arms, twinned, dished toward the hub
  const r0 = rb * 0.24, r1 = rb * 0.94, sd = 0.022;
  const arm = new THREE.Shape();
  arm.moveTo(r0, -0.022); arm.lineTo(r1, -0.015); arm.lineTo(r1, 0.015); arm.lineTo(r0, 0.022); arm.closePath();
  const armGeo = new THREE.ExtrudeGeometry(arm, { depth: sd, bevelEnabled: hq, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 1, curveSegments: 1 });
  const ap = armGeo.attributes.position;
  for (let i = 0; i < ap.count; i++) { const r = Math.hypot(ap.getX(i), ap.getY(i)); ap.setZ(i, ap.getZ(i) + (1 - r / rb) * 0.04); }
  armGeo.computeVertexNormals();
  const zs = h * 0.5;
  for (let i = 0; i < 5; i++) {
    for (const off of [-0.14, 0.14]) {
      const g = armGeo.clone();
      g.rotateZ((i / 5) * Math.PI * 2 + off);
      g.translate(0, 0, zs);
      parts.push([g, 'alloy']);
    }
  }
  const hubZ = zs + sd + 0.03;
  const hub = new THREE.CylinderGeometry(rb * 0.27, rb * 0.31, 0.04, 24);
  hub.rotateX(Math.PI / 2); hub.translate(0, 0, hubZ - 0.02);
  parts.push([hub, 'alloy']);
  parts.push(cyl(rb * 0.11, 0.012, 0, 0, hubZ + 0.004, 'chrome', 'z', 20));
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2 + Math.PI / 5;
    parts.push(cyl(0.011, 0.018, Math.cos(a) * rb * 0.19, Math.sin(a) * rb * 0.19, hubZ + 0.004, 'chrome', 'z', 6));
  }
  parts.push(cyl(rb * 0.8, 0.026, 0, 0, h * 0.02, 'rim', 'z', seg));
  parts.push(rbox(0.1, 0.2, 0.06, 0.02, caliper ? 'caliper' : 'trim', { x: rb * 0.6, y: rb * 0.38, z: h * 0.22, rz: 0.5 }));
  return parts;
}

function at(parts, x, y, z, flip = false) {
  return parts.map(([g, r]) => {
    const c = g.clone();
    if (flip) c.rotateY(Math.PI);
    c.translate(x, y, z);
    return [c, r];
  });
}

function buildCar(style, hq) {
  const st = S[style] || S.sedan;
  const F = bodyFrame(style, st, hq);
  const parts = loftBody(st, F, hq);
  const { x0, x1, plan, halfZ, span } = F;
  const tall = F.tall, W = st.W;
  const frontX = (y) => extentAt(st.body, y)[1], rearX = (y) => extentAt(st.body, y)[0];
  const lean = (fx, y) => Math.atan2(fx(y - 0.05) - fx(y + 0.05), 0.1); // > 0: the face leans back as it rises
  const gh = st.vanGlass ? null : st.gh;

  // ---- front: headlights (housing, projector, reflector, DRL), grille, intake, fog lights, plate
  // tall bodies have a vertical front/back: measure from their lowest corner points instead of the end span
  const lowest = (f) => Math.min(...st.body.filter(f).map((p) => p[1]));
  const [flo, fhi] = tall ? [lowest((p) => p[0] > x1 - 0.3), spanAt(st.body, x1 - 0.02)[1]] : spanAt(st.body, x1);
  const headY = tall ? Math.min(fhi - 0.05, flo + 0.45) : fhi - 0.03;
  const fa = lean(frontX, headY), ca = Math.cos(fa), sa = Math.sin(fa);
  const hx = frontX(headY), hh = tall ? 0.18 : 0.12, lw = W * 0.25, lz = plan(x1) * 0.58;
  for (const s of [-1, 1]) {
    const o = (d, dy = 0) => ({ x: hx - 0.025 + ca * d, y: headY + sa * d + dy, rz: fa });
    parts.push(rbox(0.06, hh, lw, 0.018, 'trim', { ...o(0), z: s * lz }));
    parts.push(rbox(0.06, hh * 0.62, lw * 0.4, 0.02, 'head', { ...o(0.008, -hh * 0.06), z: s * (lz + lw * 0.22) }));
    parts.push(rbox(0.06, hh * 0.55, lw * 0.34, 0.018, 'chrome', { ...o(0.005, -hh * 0.06), z: s * (lz - lw * 0.18) }));
    parts.push(cyl(hh * 0.2, 0.06, hx - 0.02 + ca * 0.012, headY + sa * 0.012 - hh * 0.06, s * (lz - lw * 0.18), 'head', 'x', 20));
    parts.push(rbox(0.06, 0.014, lw * 0.94, 0.006, 'head', { ...o(0.009, hh * 0.36), z: s * lz }));
  }
  const gTop = headY - (tall ? 0.12 : 0.045), gBot = flo + (tall ? 0.18 : 0.1);
  if (gTop - gBot > 0.04) {
    const gy = (gTop + gBot) / 2, gh2 = gTop - gBot, gw = W * (st.bed ? 0.58 : tall ? 0.5 : 0.42);
    const gx = frontX(gy) - 0.025, ga = lean(frontX, gy);
    parts.push(rbox(0.06, gh2, gw, 0.025, 'trim', { x: gx, y: gy, rz: ga }));
    const slats = st.bed || style === 'bigsuv' || tall ? 3 : 1;
    for (let i = 0; i < slats; i++) {
      const sy = slats === 1 ? gTop - 0.012 : gBot + (gh2 * (i + 1)) / (slats + 1);
      parts.push(rbox(0.065, slats === 1 ? 0.012 : 0.02, gw * 0.98, 0.006, 'chrome', { x: gx + 0.004, y: sy, rz: ga }));
    }
  }
  const iy = flo + 0.045;
  parts.push(rbox(0.05, 0.06, W * 0.6, 0.02, 'trim', { x: frontX(iy) - 0.015, y: iy }));
  for (const s of [-1, 1]) parts.push(cyl(0.028, 0.03, frontX(iy) - 0.005, iy + 0.005, s * W * 0.36, 'head', 'x', 20));
  const py = flo + (tall ? 0.2 : 0.11);
  parts.push(rbox(0.014, 0.15, 0.31, 0.01, 'plate', { x: frontX(py) + 0.004, y: py }));

  // ---- rear: tail lights (+ reverse lamps, light bar), plate, bumper, exhausts
  const ty = tall ? lowest((p) => p[0] < x0 + 0.3) + 0.5 : st.body[2][1] - 0.08;
  const ra = lean((y) => -rearX(y), ty);
  const tx = rearX(ty), th = tall ? 0.26 : 0.09, tw = W * 0.22, tz = plan(x0) * 0.6;
  for (const s of [-1, 1]) {
    parts.push(rbox(0.06, th, tw, 0.016, 'tail', { x: tx + 0.025, y: ty, z: s * tz, rz: -ra }));
    parts.push(rbox(0.06, th * 0.3, tw * 0.3, 0.008, 'plate', { x: tx + 0.021, y: ty - th * 0.22, z: s * (tz - tw * 0.25), rz: -ra }));
  }
  if (['sedan', 'suv', 'hatch', 'coupe'].includes(style)) parts.push(rbox(0.05, 0.018, W * 0.5, 0.008, 'tail', { x: rearX(ty + th * 0.2) + 0.02, y: ty + th * 0.2, rz: -ra }));
  const rpy = ty - (tall ? 0.35 : 0.2);
  parts.push(rbox(0.014, 0.15, 0.31, 0.01, 'plate', { x: rearX(rpy) - 0.004, y: rpy }));
  const bby = st.sill + 0.08;
  parts.push(rbox(0.05, 0.07, W * 0.84, 0.02, 'trim', { x: rearX(bby) + 0.012, y: bby }));
  if (['sedan', 'coupe', 'hatch'].includes(style)) {
    const ey = st.sill + 0.04;
    for (const s of [-1, 1]) {
      parts.push(cyl(0.036, 0.1, rearX(ey) + 0.01, ey, s * W * 0.3, 'chrome', 'x', 20));
      parts.push(cyl(0.026, 0.102, rearX(ey) + 0.01, ey, s * W * 0.3, 'liner', 'x', 16));
    }
  }
  if (st.bed) parts.push(rbox(0.02, 0.035, 0.22, 0.008, 'trim', { x: rearX(st.bl - 0.1) - 0.004, y: st.bl - 0.1 }));

  // ---- sides: mirrors, door handles, wipers, roof rails, antenna
  if (!st.windows) {
    const mx = st.gh[3][0] - 0.14;
    const my = gh ? span(mx).belt + 0.1 : st.gh[0][1] + 0.14;
    for (const s of [-1, 1]) {
      const mz = halfZ(mx, my) + 0.1;
      parts.push(rbox(0.13, 0.11, 0.17, 0.04, 'paint', { x: mx, y: my, z: s * mz }));
      parts.push(rbox(0.012, 0.085, 0.14, 0.005, 'glass', { x: mx - 0.064, y: my, z: s * mz }));
      parts.push(rbox(0.016, 0.014, 0.12, 0.005, 'amber', { x: mx + 0.05, y: my - 0.045, z: s * mz }));
      parts.push(rbox(0.08, 0.03, 0.12, 0.01, 'trim', { x: mx + 0.01, y: my - 0.04, z: s * (mz - 0.1) }));
    }
  }
  if (gh) {
    const handle = ['sedan', 'coupe', 'bigsuv', 'minivan'].includes(style) ? 'chrome' : 'trim';
    const doors = style === 'coupe' ? [F.seams[F.seams.length - 1]] : [F.seams[1], F.seams[2]];
    for (const d of doors) {
      if (d == null) continue;
      const hx2 = d + 0.17, hy = span(hx2).belt - 0.1;
      for (const s of [-1, 1]) parts.push(rbox(0.16, 0.034, 0.03, 0.012, handle, { x: hx2, y: hy, z: s * (halfZ(hx2, hy) + 0.004) }));
    }
  }
  if (F.ws) {
    const wx = F.ws[1] - 0.03, wy = span(wx).top + 0.02;
    for (const s of [-1, 1]) parts.push(rbox(0.02, 0.012, W * 0.34, 0.005, 'trim', { x: wx, y: wy, z: s * W * 0.17, ry: s * 0.12 }));
  }
  if (gh && ['suv', 'bigsuv', 'offroad', 'minivan'].includes(style)) {
    const xa = gh[0][0] + 0.25, xb = gh[2][0] - 0.12;
    for (const s of [-1, 1]) {
      const pts = [];
      for (let i = 0; i <= 8; i++) { const x = lerp(xa, xb, i / 8), [z, y] = F.at(x, 'top', 0.26); pts.push(new THREE.Vector3(x, y + 0.05, s * z)); }
      parts.push([new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 24, 0.017, 8), style === 'bigsuv' ? 'chrome' : 'trim']);
      for (const p of [pts[0], pts[8]]) parts.push(rbox(0.07, 0.06, 0.04, 0.012, 'trim', { x: p.x, y: p.y - 0.03, z: p.z }));
    }
  }
  if (gh && !tall) {
    const ax = gh[1][0] + 0.12, [, ay] = F.at(ax, 'top', 1);
    const fin = new THREE.SphereGeometry(1, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2);
    fin.scale(0.1, 0.055, 0.022); fin.translate(ax, ay - 0.004, 0);
    parts.push([fin, 'paint']);
  }
  if (st.vanGlass && !st.windows) { // cab side windows: flat glass panels just proud of the surround
    const inner = shrink(st.gh, 0.03), shape = new THREE.Shape();
    inner.forEach(([x, y], i) => (i ? shape.lineTo(x, y) : shape.moveTo(x, y)));
    const cx = inner.reduce((a, p) => a + p[0], 0) / 4, cy = inner.reduce((a, p) => a + p[1], 0) / 4;
    for (const s of [-1, 1]) {
      const g = new THREE.ExtrudeGeometry(shape, { depth: 0.008, bevelEnabled: false });
      g.translate(0, 0, s > 0 ? halfZ(cx, cy) - 0.004 : -halfZ(cx, cy) - 0.004);
      parts.push([g, 'glass']);
    }
  }
  if (st.box) parts.push(rbox(0.03, st.tall - st.sill - 0.3, W + 0.012, 0.01, 'trim', { x: st.box[1] - 0.02, y: (st.tall + st.sill) / 2 }));
  if (style === 'bus') parts.push(rbox(0.03, 1.4, W * 0.88, 0.05, 'glass', { x: x1 + 0.004, y: 2.15 }));
  if (st.spare) {
    const sp = wheelParts(st.wr * 0.9, 0.24, hq, false);
    for (const [g, r] of at(sp, 0, 0, 0)) { g.rotateY(-Math.PI / 2); g.translate(-0.13, st.bl - 0.05, 0); parts.push([g, r]); }
  }

  // ---- wheel arch lips (black cladding on SUVs and trucks)
  if (st.cladding) {
    const rr = F.R + 0.035, a0 = Math.asin(clamp((st.sill + 0.04 - st.wr) / rr, -1, 1));
    for (const ax of st.axles) {
      for (const s of [-1, 1]) {
        const pts = [];
        for (let i = 0; i <= 24; i++) {
          const a = a0 + (i / 24) * (Math.PI - 2 * a0), x = ax + Math.cos(a) * rr, y = st.wr + Math.sin(a) * rr;
          pts.push(new THREE.Vector3(x, y, s * (halfZ(x, y) + 0.006)));
        }
        parts.push([new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 32, 0.035, 8), 'trim']);
      }
    }
  }

  // ---- wheels + arch liners
  const tyreW = Math.min(0.32, W * 0.14);
  for (const ax of st.axles) {
    const liner = new THREE.CylinderGeometry(F.R * 0.98, F.R * 0.98, W - 0.16, 32, 1, true, -Math.PI / 2, Math.PI);
    liner.rotateX(Math.PI / 2); liner.rotateZ(Math.PI / 2); liner.translate(ax, st.wr, 0);
    parts.push([liner, 'liner']);
    const wp = wheelParts(st.wr, tyreW, hq, st.caliper);
    const wz = halfZ(ax, st.wr) - tyreW / 2 - 0.015;
    parts.push(...at(wp, ax, st.wr, wz));
    parts.push(...at(wp, ax, st.wr, -wz, true));
  }
  return { parts, L: st.L, H: F.top };
}

function buildMoto(hq) {
  const parts = [];
  const wr = 0.33, tw = 0.13;
  for (const x of [0.25, 1.75]) parts.push(...at(wheelParts(wr, tw, hq, false), x, wr, 0));
  const tube = (pts, r, role) => {
    const c = new THREE.CatmullRomCurve3(pts.map(([x, y]) => new THREE.Vector3(x, y, 0)));
    parts.push([new THREE.TubeGeometry(c, 40, r, 10), role]);
  };
  tube([[0.25, wr], [0.7, 0.55], [1.2, 0.5], [1.5, 0.95]], 0.035, 'trim');
  tube([[1.75, wr], [1.62, 0.75], [1.55, 1.02]], 0.03, 'chrome');
  tube([[0.4, 0.42], [0.9, 0.35], [1.2, 0.4]], 0.045, 'chrome');
  const tank = new THREE.SphereGeometry(0.22, 24, 16);
  tank.scale(1.5, 0.75, 0.8); tank.translate(1.18, 0.92, 0);
  parts.push([tank, 'paint']);
  const seat = new THREE.CapsuleGeometry(0.11, 0.42, 6, 12);
  seat.rotateZ(Math.PI / 2); seat.scale(1, 0.6, 1.2); seat.translate(0.72, 0.88, 0);
  parts.push([seat, 'trim']);
  parts.push(box(0.5, 0.35, 0.3, 0.98, 0.55, 0, 'rim'));
  const bar = new THREE.CylinderGeometry(0.018, 0.018, 0.7, 10);
  bar.rotateX(Math.PI / 2); bar.translate(1.52, 1.1, 0);
  parts.push([bar, 'chrome']);
  parts.push(box(0.06, 0.12, 0.14, 1.7, 0.98, 0, 'head'));
  parts.push(box(0.04, 0.06, 0.12, 0.2, 0.86, 0, 'tail'));
  const fender = new THREE.CylinderGeometry(wr * 1.1, wr * 1.1, 0.16, 24, 1, true, -Math.PI / 2, Math.PI * 0.8);
  fender.rotateX(Math.PI / 2); fender.rotateZ(Math.PI / 2); fender.translate(0.25, wr, 0);
  parts.push([fender, 'paint']);
  return { parts, L: 2.1, H: 1.15 };
}

const geoCache = new Map();
function merged(style, hq) {
  const key = `${style}|${hq}`;
  if (geoCache.has(key)) return geoCache.get(key);
  const { parts, L, H } = style === 'moto' ? buildMoto(hq) : buildCar(style, hq);
  const by = new Map();
  for (const [g, role] of parts) {
    const ni = g.index ? g.toNonIndexed() : g;
    for (const name of Object.keys(ni.attributes)) if (!['position', 'normal', 'uv'].includes(name)) ni.deleteAttribute(name);
    if (!ni.attributes.uv) ni.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(ni.attributes.position.count * 2), 2));
    if (!by.has(role)) by.set(role, []);
    by.get(role).push(ni);
  }
  // normalise: length 1, centred on x/z, ground (tyre bottom) at y=0
  const s = 1 / L;
  const out = [];
  for (const [role, gs] of by) {
    const g = mergeGeometries(gs, false);
    g.translate(-L / 2, 0, 0);
    g.scale(s, s, s);
    g.computeBoundingSphere();
    out.push([g, role]);
  }
  const res = { geos: out, height: H * s };
  geoCache.set(key, res);
  return res;
}

// A procedural car (length 1, facing +x) for a style. opts: {color, stencil, sir, hq}
export function proceduralCar(style, opts) {
  const { geos, height } = merged(style, !!opts.hq);
  const g = new THREE.Group();
  for (const [geo, role] of geos) {
    const m = new THREE.Mesh(geo, material(role, opts));
    m.castShadow = role !== 'liner';
    m.receiveShadow = false; // self-shadowing on the faceted body shows as acne; the ground takes the shadow
    g.add(m);
  }
  g.userData.height = height;
  return g;
}

// A TripoSR mesh (GLB written by card_meshes: already length 1, ground y=0, +x forward, vertex colours).
const loader = new GLTFLoader();
const meshCache = new Map();
export function scannedCar(url, { stencil = false } = {}) {
  const key = url.split('?')[0];
  if (!meshCache.has(key)) meshCache.set(key, loader.loadAsync(url).then((gltf) => gltf.scene));
  return meshCache.get(key).then((scene) => {
    const g = scene.clone(true);
    let h = 0;
    g.traverse((o) => {
      if (!o.isMesh) return;
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      const col = o.geometry.attributes.color;
      if (col && !o.geometry.userData.linear) {
        // trimesh writes the crop's sRGB bytes; glTF vertex colours are linear, so they'd render washed out
        const c = new THREE.Color();
        const out = new Float32Array(col.count * 3);
        for (let i = 0; i < col.count; i++) {
          c.setRGB(col.getX(i), col.getY(i), col.getZ(i), THREE.SRGBColorSpace);
          out[i * 3] = c.r; out[i * 3 + 1] = c.g; out[i * 3 + 2] = c.b;
        }
        o.geometry.setAttribute('color', new THREE.BufferAttribute(out, 3));
        o.geometry.userData.linear = true;
      }
      o.material = new THREE.MeshPhysicalMaterial({
        vertexColors: !!o.geometry.attributes.color, color: o.geometry.attributes.color ? '#ffffff' : '#9aa3ad',
        roughness: 0.55, metalness: 0.05, clearcoat: 0.35, clearcoatRoughness: 0.2, // colours are baked in
      });
      if (stencil) Object.assign(o.material, { stencilWrite: true, stencilRef: 1, stencilFunc: THREE.EqualStencilFunc });
      o.castShadow = o.receiveShadow = true;
      o.geometry.computeBoundingBox();
      h = Math.max(h, o.geometry.boundingBox.max.y);
    });
    g.userData.height = h || 0.35;
    return g;
  });
}
