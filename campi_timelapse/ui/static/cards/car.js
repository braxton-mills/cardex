// Procedural PBR cars for the Cards tab, plus loading the TripoSR meshes the background job makes.
// A style is a side silhouette (rear x=0 -> front x=L, metres, y up) with wheel arches cut out of the bottom edge, a
// greenhouse quad [rear base, rear top, front top, front base], and a few extras. It is extruded across the width
// with a bevel, narrowed above the beltline (tumblehome), dressed with glass, wheels, lights, mirrors and a grille,
// merged into one mesh per material, and normalised to length 1 / ground at y=0 / centred.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
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
    gh: [[4.3, 1.18], [4.32, 2.47], [4.36, 2.5], [4.98, 1.18]], vanGlass: true },
  boxtruck: { L: 7.2, W: 2.3, wr: 0.48, axles: [1.6, 5.6], sill: 0.6, bl: 1.45, tall: 3.3, box: [0, 5.15],
    body: [[0.02, 0.7], [0, 3.25], [0.08, 3.3], [5.15, 3.3], [5.15, 2.5], [6.75, 2.45], [6.95, 1.7], [7.2, 1.4], [7.19, 0.75], [7.1, 0.6]],
    gh: [[5.4, 1.5], [5.42, 2.4], [6.7, 2.38], [6.92, 1.5]], vanGlass: true },
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
        clearcoatRoughness: 0.035, normalMap: flakes(), normalScale: new THREE.Vector2(0.12, 0.12), envMapIntensity: 1.25,
      });
      if (sir) Object.assign(m, { iridescence: 0.55, iridescenceIOR: 1.7, iridescenceThicknessRange: [180, 620] });
      break;
    }
    case 'glass': m = new THREE.MeshPhysicalMaterial({ color: '#05070b', metalness: 0.1, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.8 }); break;
    case 'chrome': m = new THREE.MeshStandardMaterial({ color: '#d9dde3', metalness: 1, roughness: 0.14, envMapIntensity: 1.4 }); break;
    case 'rim': m = new THREE.MeshStandardMaterial({ color: '#7c838d', metalness: 1, roughness: 0.26 }); break;
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

function bodyShape(st) {
  const s = new THREE.Shape();
  const arch = st.wr * 1.2;
  const pts = st.body;
  const last = pts[pts.length - 1];
  s.moveTo(last[0], last[1]);
  // bottom edge front -> rear with arches around the axles (axles are sorted rear -> front)
  for (const ax of [...st.axles].reverse()) {
    const dy = st.sill - st.wr;
    const dx = Math.sqrt(Math.max(arch * arch - dy * dy, 0));
    const a0 = Math.atan2(dy, dx);
    s.lineTo(ax + dx, st.sill);
    s.absarc(ax, st.wr, arch, a0, Math.PI - a0, false);
  }
  s.lineTo(pts[0][0], st.sill);
  // upper contour with every corner filleted (a quadratic through the corner): rounded bumpers, roof and pillars
  // instead of a folded box, without the overshoot a spline gives on a sparse profile
  const r = st.tall ? 0.08 : 0.16;
  s.lineTo(...pts[0]);
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [x, y] = pts[i], [nx, ny] = pts[i + 1];
    const l1 = Math.hypot(x - px, y - py), l2 = Math.hypot(nx - x, ny - y);
    const k1 = Math.min(r, l1 * 0.45) / (l1 || 1), k2 = Math.min(r, l2 * 0.45) / (l2 || 1);
    s.lineTo(x - (x - px) * k1, y - (y - py) * k1);
    s.quadraticCurveTo(x, y, x + (nx - x) * k2, y + (ny - y) * k2);
  }
  s.lineTo(...pts[pts.length - 1]);
  return s;
}

function silhouette(st) {
  // body polygon with the greenhouse merged in (for extents); greenhouse sits between deck and cowl
  return [...st.body.slice(0, -1), ...st.gh, st.body[st.body.length - 1]];
}

function extrude(shape, depth, bevel, segs) {
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: depth - bevel * 2, bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel * 0.6,
    bevelSegments: segs, curveSegments: segs * 6, steps: 1,
  });
  g.translate(0, 0, -(depth - bevel * 2) / 2);
  return g;
}

function tumble(g, from, to, k) {
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const t = THREE.MathUtils.clamp((p.getY(i) - from) / Math.max(to - from, 0.01), 0, 1);
    p.setZ(i, p.getZ(i) * (1 - k * t * t));
  }
  g.computeVertexNormals();
  return g;
}

function quad(pts) {
  const s = new THREE.Shape();
  s.moveTo(...pts[0]);
  for (const p of pts.slice(1)) s.lineTo(...p);
  return s;
}

function shrink(pts, inset) {
  const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length, cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  return pts.map(([x, y]) => {
    const dx = cx - x, dy = cy - y, l = Math.hypot(dx, dy) || 1;
    return [x + (dx / l) * inset * 1.4, y + (dy / l) * inset];
  });
}

// Wheel at the origin, axis along z (outer face +z)
function wheelParts(wr, tw, hq, caliper) {
  const parts = [];
  const seg = hq ? 64 : 32;
  const prof = [];
  const rb = wr * 0.64;
  const n = 10;
  for (let i = 0; i <= n; i++) { const a = -Math.PI / 2 + (i / n) * Math.PI; prof.push(new THREE.Vector2(wr - 0.05 + Math.cos(a) * 0.05, (tw / 2 - 0.05) * Math.sin(a) + Math.sin(a) * 0.05)); }
  prof.unshift(new THREE.Vector2(rb, -tw / 2 + 0.01));
  prof.push(new THREE.Vector2(rb, tw / 2 - 0.01));
  const tire = new THREE.LatheGeometry(prof, seg);
  tire.rotateX(Math.PI / 2);
  parts.push([tire, 'tire']);
  const barrel = new THREE.CylinderGeometry(rb, rb, tw * 0.9, seg, 1, true);
  barrel.rotateX(Math.PI / 2);
  parts.push([barrel, 'rim']);
  const face = new THREE.CylinderGeometry(rb * 0.98, rb * 0.98, 0.02, seg);
  face.rotateX(Math.PI / 2); face.translate(0, 0, -tw * 0.1);
  parts.push([face, 'trim']);
  const spokes = 6;
  for (let i = 0; i < spokes; i++) {
    const sp = new THREE.BoxGeometry(rb * 0.92, 0.055, 0.04);
    sp.translate(rb * 0.48, 0, 0);
    sp.rotateZ((i / spokes) * Math.PI * 2);
    sp.translate(0, 0, tw * 0.36);
    parts.push([sp, 'chrome']);
  }
  const lip = new THREE.TorusGeometry(rb * 0.97, 0.018, 8, seg);
  lip.translate(0, 0, tw * 0.4);
  parts.push([lip, 'chrome']);
  const hub = new THREE.CylinderGeometry(rb * 0.2, rb * 0.24, 0.06, 24);
  hub.rotateX(Math.PI / 2); hub.translate(0, 0, tw * 0.38);
  parts.push([hub, 'chrome']);
  const rotor = new THREE.CylinderGeometry(rb * 0.82, rb * 0.82, 0.03, seg);
  rotor.rotateX(Math.PI / 2); rotor.translate(0, 0, tw * 0.1);
  parts.push([rotor, 'rim']);
  if (caliper) {
    const c = new THREE.BoxGeometry(0.12, 0.22, 0.07);
    c.translate(rb * 0.62, rb * 0.3, tw * 0.18);
    parts.push([c, 'caliper']);
  }
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

function box(w, h, d, x, y, z, role) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(x, y, z);
  return [g, role];
}

function buildCar(style, hq) {
  const st = S[style] || S.sedan;
  const parts = [];
  const segs = hq ? 5 : 3;
  const bevel = Math.min(0.09, st.W * 0.045);
  const top = Math.max(...st.gh.map((p) => p[1]), ...st.body.map((p) => p[1]));
  const sil = silhouette(st);

  // body (silhouette incl. greenhouse; pillars and roof are paint)
  const body = tumble(extrude(bodyShape({ ...st, body: sil }), st.W, bevel, segs), st.bl, top, st.tall ? 0.04 : 0.17);
  parts.push([body, 'paint']);

  // glass
  const k = st.tall ? 0.04 : 0.17;
  if (st.windows) {
    const [x0, x1, y0, y1, n] = st.windows;
    const w = (x1 - x0) / n;
    for (let i = 0; i < n; i++) {
      const g = extrude(quad([[x0 + i * w + 0.08, y0], [x0 + i * w + 0.08, y1], [x0 + (i + 1) * w - 0.08, y1], [x0 + (i + 1) * w - 0.08, y0]]), st.W + 0.02, 0.006, 1);
      parts.push([g, 'glass']);
    }
  }
  const [rb, rt, ft, fb] = st.gh;
  if (st.vanGlass) {
    // cab: side windows plus the windscreen as an inset slab on the slope
    parts.push([tumble(extrude(quad(shrink(st.gh, 0.07)), st.W + 0.02, 0.006, 1), st.bl, top, k), 'glass']);
  } else {
    // side windows split by the B-pillar
    const inner = shrink(st.gh, 0.075);
    const mid = (inner[1][0] + inner[2][0]) / 2 + (fb[0] - rb[0]) * 0.04;
    const yAt = (a, b, x) => a[1] + ((x - a[0]) / (b[0] - a[0] || 1)) * (b[1] - a[1]);
    const backWin = [inner[0], inner[1], [mid - 0.05, yAt(inner[1], inner[2], mid - 0.05)], [mid - 0.05, inner[0][1]]];
    const frontWin = [[mid + 0.05, inner[3][1]], [mid + 0.05, yAt(inner[1], inner[2], mid + 0.05)], inner[2], inner[3]];
    for (const w of [backWin, frontWin]) parts.push([tumble(extrude(quad(w), st.W + 0.016, 0.006, 1), st.bl, top, k), 'glass']);
  }
  // windscreen and rear window: slabs offset outward from the slope
  const slope = (a, b, off) => {
    const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = dy / l, ny = -dx / l; // outward for a CW walk
    const o = bevel * 0.6 + off;
    return [[a[0] + nx * 0.005, a[1] + ny * 0.005], [b[0] + nx * 0.005, b[1] + ny * 0.005], [b[0] + nx * o, b[1] + ny * o], [a[0] + nx * o, a[1] + ny * o]];
  };
  const lerp2 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const ws = slope(lerp2(ft, fb, 0.06), lerp2(ft, fb, 0.92), 0.014);
  parts.push([tumble(extrude(quad(ws), st.W * 0.84, 0.01, 1), st.bl, top, k), 'glass']);
  if (!st.vanGlass && !st.bed) {
    const rw = slope(lerp2(rb, rt, 0.1), lerp2(rb, rt, 0.9), 0.014);
    parts.push([tumble(extrude(quad(rw), st.W * 0.82, 0.01, 1), st.bl, top, k), 'glass']);
  }
  if (st.bed) {
    // cab back window + the open bed
    const cabBack = [[st.bed[1] + 0.12, st.bl + 0.1], [st.bed[1] + 0.12, top - 0.12]];
    parts.push(box(0.04, cabBack[1][1] - cabBack[0][1], st.W * 0.62, st.gh[0][0] + 0.03, (cabBack[0][1] + cabBack[1][1]) / 2, 0, 'glass'));
    const [b0, b1] = st.bed;
    parts.push(box(b1 - b0 - 0.12, 0.02, st.W - 0.2, (b0 + b1) / 2, st.bl - 0.015, 0, 'trim'));
    parts.push(box(0.05, 0.03, st.W - 0.02, b0 + 0.03, st.bl - 0.01, 0, 'trim'));
  }
  if (st.box) {
    const [x0, x1] = st.box;
    parts.push(box(0.03, st.tall - st.sill - 0.3, st.W + 0.01, x1 - 0.02, (st.tall + st.sill) / 2, 0, 'trim'));
    parts.push(box(x1 - x0 - 0.1, 0.05, st.W + 0.01, (x0 + x1) / 2, st.sill + 0.25, 0, 'trim'));
  }

  // lights
  const fy = (st.body[st.body.length - 3][1] + st.body[st.body.length - 4][1]) / 2;
  const headY = Math.min(fy, st.bl - 0.12);
  const [, fx] = extentAt(sil, headY);
  const ev = style === 'suv' || style === 'sedan';
  for (const z of [-1, 1]) {
    parts.push(box(0.1, st.tall ? 0.14 : 0.08, st.W * 0.2, fx - 0.04, headY, z * st.W * 0.34, 'head'));
    const ty = Math.min(st.body[2][1] - 0.08, st.bl - 0.1);
    const [tx] = extentAt(sil, ty);
    parts.push(box(0.1, st.tall ? 0.28 : 0.09, st.W * 0.18, tx + 0.04, ty, z * st.W * 0.36, 'tail'));
    // mirrors
    if (!st.windows) parts.push(box(0.16, 0.1, 0.16, fb[0] - 0.12, st.bl + 0.1, z * (st.W / 2 + 0.06), 'paint'));
  }
  if (ev && !st.bed) parts.push(box(0.06, 0.018, st.W * 0.5, extentAt(sil, headY + 0.06)[1] - 0.02, headY + 0.06, 0, 'head'));
  // grille + lower bumper trim
  const gy = headY - (st.tall ? 0.3 : 0.17);
  parts.push(box(0.1, st.bed || st.tall ? 0.34 : 0.14, st.W * (st.bed ? 0.56 : 0.44), extentAt(sil, gy)[1] - 0.04, gy, 0, st.bed ? 'chrome' : 'trim'));
  if (st.cladding) {
    parts.push(box(st.L * 0.98, 0.1, st.W + 0.01, st.L / 2, st.sill + 0.05, 0, 'trim'));
  }
  parts.push(box(st.L * 0.94, 0.06, st.W * 0.9, st.L / 2, st.sill - 0.02, 0, 'trim'));
  if (st.spare) {
    const sp = wheelParts(st.wr * 0.9, 0.24, hq, false);
    for (const [g, r] of at(sp, 0, 0, 0)) { g.rotateY(-Math.PI / 2); g.translate(-0.13, st.bl - 0.05, 0); parts.push([g, r]); }
  }

  // wheels + arch liners
  const tw = Math.min(0.32, st.W * 0.14);
  const wz = st.W / 2 - tw / 2 - 0.02;
  for (const ax of st.axles) {
    const liner = new THREE.CylinderGeometry(st.wr * 1.17, st.wr * 1.17, st.W - 0.16, 32, 1, true, -Math.PI / 2, Math.PI);
    liner.rotateX(Math.PI / 2); liner.rotateZ(Math.PI / 2); liner.translate(ax, st.wr, 0);
    parts.push([liner, 'liner']);
    const wp = wheelParts(st.wr, tw, hq, st.caliper);
    parts.push(...at(wp, ax, st.wr, wz));
    parts.push(...at(wp, ax, st.wr, -wz, true));
  }
  return { parts, L: st.L, H: top };
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
    m.receiveShadow = true;
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
