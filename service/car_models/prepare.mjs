// Prepares a downloaded Sketchfab car (.glb or .gltf) for the Cards tab: the same frame as the procedural cars and
// the TripoSR scans (length 1 along +x = the car's front, y up, ground at y = 0, centred), number plates and studio
// floors removed, simplified to a face budget, textures resized to WebP, meshopt-compressed.
//
// Usage: node prepare.mjs <in.glb|in.gltf> <out.glb> [--yaw DEG] [--faces N] [--texture PX]
//   --yaw: extra turn about y (90° steps) on top of the automatic guess; campi models passes the user's pick.
// Prints one line of JSON on stdout: sizes, how the front was found, and per-material stats (campi models picks
// the paint material from them).

import { writeFile } from 'node:fs/promises';
import { Logger, NodeIO, getBounds } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, flatten, join, meshopt, prune, simplify, textureCompress, weld } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import draco3d from 'draco3dgltf';
import sharp from 'sharp';

const args = process.argv.slice(2);
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? Number(args.splice(i, 2)[1]) : def;
};
const extraYaw = opt('yaw', 0);
const maxFaces = opt('faces', 150_000);
const texturePx = opt('texture', 1024);
const [input, output] = args;
if (!input || !output) {
  console.error('usage: node prepare.mjs <in> <out.glb> [--yaw DEG] [--faces N] [--texture PX]');
  process.exit(2);
}

// Parts a card never shows: plates (Sketchfab models often carry the modeller's name on them) and studio sets.
const DROP = new RegExp([
  'licen[cs]e', 'lic ?plate', 'number ?plate', 'reg ?plate', '(^|[^a-z])plates?([^a-z]|$)',
  'kennzeichen', 'nummernschild', 'shadow', 'ground', 'floor', 'backdrop', 'turntable', 'platform', 'studio', 'environment',
].join('|'), 'i');
const FRONT = /head ?(light|lamp)|front|grill|grille|windshield|windscreen|steering|hood|bonnet/i;
const REAR = /tail ?(light|lamp|gate)|rear(?!view)|brake ?light|red ?glass|exhaust|muffler|trunk|boot|tailpipe/i;

await MeshoptDecoder.ready;
await MeshoptEncoder.ready;
await MeshoptSimplifier.ready;
// gltf-transform logs to stdout; only warnings and errors, and on stderr, so stdout stays one line of JSON.
const logger = new Logger(Logger.Verbosity.WARN);
logger.warn = (m) => console.error(m);
logger.error = (m) => console.error(m);
const io = new NodeIO()
  .setLogger(logger)
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({
    'meshopt.decoder': MeshoptDecoder,
    'meshopt.encoder': MeshoptEncoder,
    'draco3d.decoder': await draco3d.createDecoderModule(),
  });

const doc = (await io.read(input)).setLogger(logger);
const root = doc.getRoot();
const scene = root.getDefaultScene() || root.listScenes()[0];
for (const s of root.listScenes()) if (s !== scene) s.dispose();

// --- drop plates and studio sets --------------------------------------------------------------------------------

const removed = [];
const allNodes = () => {
  const out = [];
  scene.traverse((n) => out.push(n));
  return out;
};
const meshNodes = () => allNodes().filter((n) => n.getMesh());
const primFaces = (p) => (p.getIndices()?.getCount() ?? p.getAttribute('POSITION')?.getCount() ?? 0) / 3;
const meshFaces = (mesh) => mesh.listPrimitives().reduce((u, p) => u + primFaces(p), 0);
const facesOf = () => meshNodes().reduce((t, n) => t + meshFaces(n.getMesh()), 0);

// By name: a whole node when the node or mesh is named like one, else just the primitives whose material is. A
// match that would take more than a third of the model is a naming accident (a root called "Environment"), so none go.
{
  const nodes = [], prims = new Set();
  let faces = 0;
  for (const n of meshNodes()) {
    const mesh = n.getMesh();
    if (DROP.test(`${n.getName()} ${mesh.getName()}`)) {
      nodes.push(n);
      faces += meshFaces(mesh);
      continue;
    }
    for (const p of mesh.listPrimitives()) {
      if (DROP.test(p.getMaterial()?.getName() ?? '') && !prims.has(p)) {
        prims.add(p);
        faces += primFaces(p);
      }
    }
  }
  if (faces < facesOf() / 3) {
    for (const n of nodes) { removed.push(n.getName() || n.getMesh().getName()); n.dispose(); }
    for (const p of prims) { removed.push(p.getMaterial().getName()); p.dispose(); }
  }
}
// A big flat slab under everything is a floor even when it isn't named one.
{
  const all = getBounds(scene);
  const nodes = meshNodes();
  for (const n of nodes) {
    const b = getBounds(n);
    const dx = b.max[0] - b.min[0], dy = b.max[1] - b.min[1], dz = b.max[2] - b.min[2];
    const flat = dy < 0.01 * Math.max(dx, dz);
    const huge = dx > 0.95 * (all.max[0] - all.min[0]) && dz > 0.95 * (all.max[2] - all.min[2]);
    if (flat && huge && b.min[1] <= all.min[1] + 1e-6 && nodes.length > 1) {
      removed.push(n.getName() || 'floor');
      n.dispose();
    }
  }
}

// --- materials: no transmission (an extra scene pass per card in three.js), no all-transparent placeholders --------

const GLASS = /glass|window|windshield|windscreen|lens|lamp|light|clear|transparent/i;
const WINDOW = /glass|window|windshield|windscreen/i;
const LAMP = /lens|lamp|light|mirror/i;
for (const ext of root.listExtensionsUsed()) {
  if (['KHR_materials_transmission', 'KHR_materials_volume'].includes(ext.extensionName)) ext.dispose();
}
{
  // Some uploads blend every material at partial alpha (the source's preview look); then only glass stays blended.
  let blended = 0, total = 0;
  for (const n of meshNodes()) for (const p of n.getMesh().listPrimitives()) {
    const f = primFaces(p);
    total += f;
    if (p.getMaterial()?.getAlphaMode() === 'BLEND') blended += f;
  }
  for (const mat of root.listMaterials()) {
    // Opaque window glass (some exports drop the transparency) reads as grey paint: make it dark tinted glass.
    const name = mat.getName();
    if (mat.getAlphaMode() === 'OPAQUE' && WINDOW.test(name) && !LAMP.test(name) && !mat.getBaseColorTexture()) {
      mat.setAlphaMode('BLEND').setBaseColorFactor([0.02, 0.03, 0.035, 0.6])
        .setMetallicFactor(0).setRoughnessFactor(0.05);
      continue;
    }
    if (mat.getAlphaMode() !== 'BLEND') continue;
    if (GLASS.test(mat.getName())) {
      const c = mat.getBaseColorFactor();
      mat.setBaseColorFactor([c[0], c[1], c[2], Math.min(Math.max(c[3], 0.3), 0.75)]);
    } else if (blended > total / 2 && !mat.getBaseColorTexture()) {
      const c = mat.getBaseColorFactor();
      mat.setAlphaMode('OPAQUE').setBaseColorFactor([c[0], c[1], c[2], 1]);
    }
  }
}

// --- orient: long axis to +x, front to +x ---------------------------------------------------------------------

const wrapper = doc.createNode('card');
for (const child of scene.listChildren()) {
  scene.removeChild(child);
  wrapper.addChild(child);
}
scene.addChild(wrapper);

let b = getBounds(scene);
const ext = [0, 1, 2].map((i) => b.max[i] - b.min[i]);
const longX = ext[0] >= ext[2];
const longAxis = longX ? 0 : 2;
const mid = (b.max[longAxis] + b.min[longAxis]) / 2;

// World-space vertex centroid of the parts named like the front or the rear, along the long axis.
function centroidAlong(re) {
  let sum = 0, n = 0;
  for (const node of meshNodes()) {
    const m = node.getWorldMatrix();
    for (const prim of node.getMesh().listPrimitives()) {
      const name = `${node.getName()} ${node.getMesh().getName()} ${prim.getMaterial()?.getName() ?? ''}`;
      if (!re.test(name)) continue;
      const pos = prim.getAttribute('POSITION');
      if (!pos) continue;
      const v = [0, 0, 0];
      const step = Math.max(1, Math.floor(pos.getCount() / 2000));
      for (let i = 0; i < pos.getCount(); i += step) {
        pos.getElement(i, v);
        const w = [0, 1, 2].map((r) => m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r]);
        sum += w[longAxis] - mid;
        n++;
      }
    }
  }
  return n ? sum / n : null;
}
const f = centroidAlong(FRONT), r = centroidAlong(REAR);
let sign, front;
if (f !== null && r !== null && Math.abs(f - r) > 0.05 * ext[longAxis]) {
  sign = Math.sign(f - r); front = 'front+rear names';
} else if (f !== null && Math.abs(f) > 0.1 * ext[longAxis]) {
  sign = Math.sign(f); front = 'front names';
} else if (r !== null && Math.abs(r) > 0.1 * ext[longAxis]) {
  sign = -Math.sign(r); front = 'rear names';
} else {
  sign = 1; front = longX ? 'none' : 'gltf +z'; // glTF assets face +z by convention
}
// yaw about +y turning the front direction onto +x: +z -> +x is +90°, -z -> -90°, -x -> 180°
const autoYaw = longX ? (sign > 0 ? 0 : 180) : (sign > 0 ? 90 : -90);
const yaw = (((autoYaw + extraYaw) % 360) + 360) % 360;
const rad = (yaw * Math.PI) / 180;
wrapper.setRotation([0, Math.sin(rad / 2), 0, Math.cos(rad / 2)]);

// --- normalise: length 1, centred, ground at y = 0 --------------------------------------------------------------

b = getBounds(scene);
const length = b.max[0] - b.min[0];
const s = 1 / length;
wrapper.setScale([s, s, s]);
b = getBounds(scene);
wrapper.setTranslation([-(b.max[0] + b.min[0]) / 2, -b.min[1], -(b.max[2] + b.min[2]) / 2]);
b = getBounds(scene);

// --- per-material stats (in the card frame, so areas compare across models) ------------------------------------

const facesIn = Math.round(facesOf());

const stats = new Map();
for (const node of meshNodes()) {
  const m = node.getWorldMatrix();
  for (const prim of node.getMesh().listPrimitives()) {
    const mat = prim.getMaterial();
    const pos = prim.getAttribute('POSITION');
    if (!pos || prim.getMode() !== 4) continue;
    const key = mat?.getName() || '(none)';
    const st = stats.get(key) || { name: key, area: 0, ySum: 0, yW: 0 };
    const idx = prim.getIndices();
    const count = idx ? idx.getCount() : pos.getCount();
    const a = [0, 0, 0], bb = [0, 0, 0], c = [0, 0, 0];
    const world = (v) => [0, 1, 2].map((r) => m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r]);
    const step = Math.max(3, Math.floor(count / 3 / 20000) * 3);
    for (let i = 0; i + 2 < count; i += step) {
      pos.getElement(idx ? idx.getScalar(i) : i, a);
      pos.getElement(idx ? idx.getScalar(i + 1) : i + 1, bb);
      pos.getElement(idx ? idx.getScalar(i + 2) : i + 2, c);
      const [p, q, w] = [world(a), world(bb), world(c)];
      const u = [q[0] - p[0], q[1] - p[1], q[2] - p[2]], v = [w[0] - p[0], w[1] - p[1], w[2] - p[2]];
      const cr = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const ar = Math.hypot(...cr) / 2 * (step / 3);
      st.area += ar;
      st.ySum += ar * (p[1] + q[1] + w[1]) / 3;
      st.yW += ar;
    }
    if (mat) {
      st.color = mat.getBaseColorFactor().map((x) => +x.toFixed(3));
      st.alpha = mat.getAlphaMode();
      st.textured = !!mat.getBaseColorTexture();
      st.metallic = +mat.getMetallicFactor().toFixed(3);
      st.roughness = +mat.getRoughnessFactor().toFixed(3);
      st.transmission = !!mat.getExtension('KHR_materials_transmission');
    }
    stats.set(key, st);
  }
}
const materials = [...stats.values()].map(({ ySum, yW, ...st }) => ({
  ...st, area: +st.area.toFixed(4), y: yW ? +(ySum / yW).toFixed(3) : null,
})).sort((p, q) => q.area - p.area);

// --- optimise ---------------------------------------------------------------------------------------------------

await doc.transform(
  prune(),
  dedup(),
  weld(),
  // one primitive per material: Sketchfab cars come in hundreds of parts, and a grid of cards draws each one
  flatten(),
  join({ keepNamed: false }),
  ...(facesIn > maxFaces ? [simplify({ simplifier: MeshoptSimplifier, ratio: maxFaces / facesIn, error: 0.002 })] : []),
  textureCompress({ encoder: sharp, targetFormat: 'webp', resize: [texturePx, texturePx] }),
  prune(),
  meshopt({ encoder: MeshoptEncoder, level: 'medium' }),
);
const facesOut = Math.round(facesOf());
// No textures and at most two colours: an untextured export (default grey, or 3ds Max wire colours) that reads as
// a ghost on a card. campi models tries the next candidate instead.
const rgb = (m) => m.getBaseColorFactor().slice(0, 3).map((x) => x.toFixed(2)).join();
const colours = new Set(root.listMaterials().map(rgb));
const plain = root.listTextures().length === 0 && colours.size <= 2;
// always one binary file, whatever the output name (campi models writes to a .tmp first)
await writeFile(output, await io.writeBinary(doc));

console.log(JSON.stringify({
  faces_in: facesIn, faces_out: facesOut,
  source_length: +length.toFixed(4),
  height: +(b.max[1] - b.min[1]).toFixed(4), // in the card frame (length 1)
  width: +(b.max[2] - b.min[2]).toFixed(4),
  yaw, auto_yaw: autoYaw, front, removed, plain, materials,
}));
