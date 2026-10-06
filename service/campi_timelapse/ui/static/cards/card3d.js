// One trading card as a real 3D object with its own scene and camera.
// The art window is a stencil portal: a mask quad writes stencil 1, and the diorama (backdrop, road, car) sits behind
// the card plane and only draws where stencil == 1, so tilting the card gives true parallax. The printed face
// (alpha 0 inside the window) and the additive foil layer go on top. Every car is stencilled to the window and sits
// entirely behind the card plane, so tilting never shows it through the face, past the edges or out of the back.
import * as THREE from 'three';
import { CARD_H, CARD_W, drawBack, drawFace, drawGround } from './face.js';
import { paintBackdrop } from './art.js';
import { faceMaterial, foilMaterial } from './shaders.js';
import { material, proceduralCar, scannedCar } from './car.js';

export const W = CARD_W / CARD_H; // card width when height = 1
const MM = 1 / CARD_H;
const THICK = 0.0045;
const GRID_DIST = 2.6; // grid camera distance: where fit() put it for a resting card (fov 26, card ~83% of its viewport)

function tex(canvas, renderer, srgb = true) {
  const t = new THREE.CanvasTexture(canvas);
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  return t;
}

function roundedRect(w, h, r) {
  const s = new THREE.Shape();
  s.moveTo(-w / 2 + r, -h / 2);
  s.lineTo(w / 2 - r, -h / 2); s.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
  s.lineTo(w / 2, h / 2 - r); s.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
  s.lineTo(-w / 2 + r, h / 2); s.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
  s.lineTo(-w / 2, -h / 2 + r); s.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
  return s;
}

// shape geometry with uvs mapped onto the card rectangle (so face/mask textures line up with a rounded outline)
function cardGeometry(r) {
  const g = new THREE.ShapeGeometry(roundedRect(W, 1, r), 12);
  const p = g.attributes.position, uv = g.attributes.uv;
  for (let i = 0; i < p.count; i++) uv.setXY(i, p.getX(i) / W + 0.5, p.getY(i) + 0.5);
  return g;
}

const STENCIL_EQ = { stencilWrite: true, stencilRef: 1, stencilFunc: THREE.EqualStencilFunc, stencilFail: THREE.KeepStencilOp, stencilZFail: THREE.KeepStencilOp, stencilZPass: THREE.KeepStencilOp };

const backTex = new WeakMap();
const sceneTexs = new WeakMap(); // renderer -> Map(key -> texture), shared by every card showing that scene
function sceneTex(renderer, typeKey, finish, variant) {
  if (!sceneTexs.has(renderer)) sceneTexs.set(renderer, new Map());
  const m = sceneTexs.get(renderer), key = `${typeKey}|${finish}|${variant}`;
  if (!m.has(key)) m.set(key, tex(paintBackdrop(typeKey, finish, variant), renderer));
  return m.get(key);
}

export class Card3D {
  // opts: {renderer, env, quality: {hq, shadows, shadowSize}, gpu, total, px, inspector, scanned}
  constructor(item, opts) {
    this.item = item;
    this.opts = opts;
    this.finish = item.count > 0 ? (item.finish || 'plain') : 'none';
    this.full = this.finish === 'fullart' || this.finish === 'sir';
    this.sir = this.finish === 'sir';
    this.scene = new THREE.Scene();
    this.scene.environment = opts.env;
    this.scene.environmentIntensity = 0.75;
    this.camera = new THREE.PerspectiveCamera(26, 1, 0.05, 20);
    this.camera.position.set(0, 0, 3);
    this.root = new THREE.Group();
    this.scene.add(this.root);
    // spring state: current/target tilt (radians), velocity, lift
    this.s = { rx: 0, ry: 0, vrx: 0, vry: 0, trx: 0, try: 0, lift: 0, vlift: 0, tlift: 0 };
    this.light = new THREE.Vector3(-0.6, 0.9, 1.6);
    this.lightTarget = this.light.clone();
    this.spin = Math.random() * Math.PI * 2;
    this.disposables = [];
    this.build();
  }

  build() {
    const { renderer, px = 750 } = this.opts;
    const item = this.item;
    const d = drawFace(item, { px, total: this.opts.total, gpu: this.opts.gpu });
    this.type = d.type;
    const faceT = tex(d.face, renderer), maskT = tex(d.mask, renderer, false);
    this.disposables.push(faceT, maskT);

    // art window rectangle in card units (centre-origin, y up)
    const a = d.art;
    const aw = a.w * MM, ah = a.h * MM;
    const ax = -W / 2 + (a.x + a.w / 2) * MM, ay = 0.5 - (a.y + a.h / 2) * MM;
    this.art = { w: aw, h: ah, x: ax, y: ay };

    // 1. stencil mask for the art window
    const maskGeo = new THREE.ShapeGeometry(roundedRect(aw, ah, a.r * MM), 8);
    const maskMat = new THREE.MeshBasicMaterial({
      colorWrite: false, depthWrite: false, stencilWrite: true, stencilRef: 1, stencilFunc: THREE.AlwaysStencilFunc,
      stencilZPass: THREE.ReplaceStencilOp,
    });
    const maskMesh = new THREE.Mesh(maskGeo, maskMat);
    maskMesh.position.set(ax, ay, 0.0005);
    maskMesh.renderOrder = -10;
    this.root.add(maskMesh);
    this.disposables.push(maskGeo, maskMat);

    // 2. the diorama behind the window
    const dio = new THREE.Group();
    dio.position.set(ax, this.full ? ay : ay - ah * 0.08, 0);
    this.root.add(dio);
    this.dio = dio;
    const back = new THREE.Mesh(new THREE.PlaneGeometry(aw * 2.6, aw * 2.6 * 1.25), new THREE.MeshBasicMaterial({ ...STENCIL_EQ, toneMapped: false }));
    back.position.set(0, ah * 0.25, -0.75);
    dio.add(back);
    this.backdrop = back;
    // illustrated scenery for the card's type (shared texture per scene; never disposed with the card)
    const variant = [...(item.label || '')].reduce((a, ch) => a + ch.charCodeAt(0), 0) % 2;
    back.material.map = sceneTex(renderer, d.type.key, this.finish, variant);
    back.material.color.set(this.finish === 'none' ? '#5a5e66' : this.full ? '#e4e6ea' : '#eceef0'); // keeps the sky under the bloom threshold
    this.disposables.push(back.geometry, back.material);
    const groundY = this.full ? -0.07 : -ah * 0.36; // full art: the car sits between the header and the moves
    const groundT = tex(drawGround(d.type, this.full), renderer);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(aw * 2.2, 1.2), new THREE.MeshStandardMaterial({
      ...STENCIL_EQ, map: groundT, transparent: true, roughness: 0.55, metalness: 0.1, depthWrite: false,
    }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(0, groundY, -0.45);
    ground.renderOrder = -5;
    dio.add(ground);
    this.disposables.push(ground.geometry, ground.material, groundT);
    if (this.opts.quality.shadows) {
      const sh = new THREE.Mesh(new THREE.PlaneGeometry(aw * 1.6, 0.9), new THREE.ShadowMaterial({ ...STENCIL_EQ, opacity: 0.55 }));
      sh.rotation.x = -Math.PI / 2;
      sh.position.set(0, groundY + 0.0005, -0.4);
      sh.receiveShadow = true;
      sh.renderOrder = -4;
      dio.add(sh);
      this.disposables.push(sh.geometry, sh.material);
    }
    // contact shadow blob (always; cheap)
    const blob = document.createElement('canvas');
    blob.width = blob.height = 128;
    const bx = blob.getContext('2d');
    const bg = bx.createRadialGradient(64, 64, 4, 64, 64, 64);
    bg.addColorStop(0, 'rgba(0,0,0,.75)'); bg.addColorStop(1, 'rgba(0,0,0,0)');
    bx.fillStyle = bg; bx.fillRect(0, 0, 128, 128);
    const blobT = new THREE.CanvasTexture(blob);
    const blobMesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ ...STENCIL_EQ, map: blobT, transparent: true, depthWrite: false }));
    blobMesh.rotation.x = -Math.PI / 2;
    blobMesh.renderOrder = -3;
    this.blob = blobMesh;
    dio.add(blobMesh);
    this.disposables.push(blobMesh.geometry, blobMesh.material, blobT);

    // car turntable
    this.carHolder = new THREE.Group();
    this.carLen = aw * (this.full ? 0.86 : 0.8);
    // deep enough that the car's turning radius never reaches the card plane (z=0)
    this.carHolder.position.set(0, groundY, -(this.carLen * 0.55 + 0.06));
    dio.add(this.carHolder);
    this.groundY = groundY;
    this.setCar(this.opts.scanned && item.mesh ? 'scanned' : 'procedural');

    // lights for the car (environment does most of the work)
    const key = new THREE.DirectionalLight('#fff4e6', 1.7);
    key.position.set(-0.8, 1.4, 1.2);
    key.target = this.carHolder;
    if (this.opts.quality.shadows) {
      key.castShadow = true;
      key.shadow.mapSize.set(this.opts.quality.shadowSize, this.opts.quality.shadowSize);
      key.shadow.camera.left = key.shadow.camera.bottom = -0.6;
      key.shadow.camera.right = key.shadow.camera.top = 0.6;
      key.shadow.camera.near = 0.1; key.shadow.camera.far = 4;
      key.shadow.bias = -0.0004;
      key.shadow.radius = 4;
    }
    dio.add(key);
    const rim = new THREE.DirectionalLight(this.finish === 'none' ? '#9fb4ff' : '#bcd3ff', this.finish === 'none' ? 3.5 : 1.4);
    rim.position.set(0.9, 0.6, -1.4);
    rim.target = this.carHolder;
    dio.add(rim);
    this.scene.add(new THREE.HemisphereLight('#dfe8ff', '#2a2420', 0.5));
    // diorama: look down at the road a little
    dio.rotation.x = 0.3;

    // 3. printed face
    const geo = cardGeometry(3 * MM);
    this.face = new THREE.Mesh(geo, faceMaterial(faceT));
    this.face.renderOrder = 2;
    this.root.add(this.face);
    // 4. foil layer
    this.foil = new THREE.Mesh(geo, foilMaterial(maskT, this.finish));
    this.foil.position.z = 0.0004;
    this.foil.renderOrder = 3;
    this.root.add(this.foil);
    this.disposables.push(geo, this.face.material, this.foil.material);
    // 5. edge (a thin ring so it never hides the art) + back
    const ring = roundedRect(W, 1, 3 * MM);
    ring.holes.push(roundedRect(W - 0.003, 1 - 0.003, 3 * MM - 0.0015));
    const edgeGeo = new THREE.ExtrudeGeometry(ring, { depth: THICK, bevelEnabled: false, curveSegments: 10 });
    edgeGeo.translate(0, 0, -THICK);
    const edgeMat = new THREE.MeshStandardMaterial({ color: this.full ? '#d9dde4' : '#e8c34a', roughness: 0.35, metalness: 0.4 });
    this.root.add(new THREE.Mesh(edgeGeo, edgeMat));
    let bt = backTex.get(renderer);
    if (!bt) { bt = tex(drawBack(), renderer); backTex.set(renderer, bt); }
    const backMat = new THREE.MeshStandardMaterial({ map: bt, roughness: 0.45, metalness: 0.05 });
    const backMesh = new THREE.Mesh(geo, backMat);
    backMesh.rotation.y = Math.PI;
    backMesh.position.z = -THICK;
    this.backMesh = backMesh;
    this.root.add(backMesh);
    this.disposables.push(edgeGeo, edgeMat, backMat);
  }

  setCar(kind) {
    this.carKind = kind;
    const stencil = true;
    const place = (car) => {
      if (this.disposed) return;
      if (this.car) this.carHolder.remove(this.car);
      car.scale.setScalar(this.carLen);
      if (this.finish === 'none') car.traverse((o) => { if (o.isMesh) o.material = material('silhouette', { stencil }); });
      this.car = car;
      this.carHolder.add(car);
      const h = (car.userData.height || 0.35) * this.carLen;
      this.blob.scale.set(this.carLen * 1.15, this.carLen * 0.55, 1);
      this.blob.position.set(0, this.groundY + 0.001, this.carHolder.position.z);
      this.carHeight = h;
    };
    if (kind === 'scanned' && this.item.mesh) {
      scannedCar(this.item.mesh.url, { stencil }).then(place).catch(() => place(this.procedural()));
      if (!this.car) place(this.procedural());
    } else {
      place(this.procedural());
    }
  }

  procedural() {
    return proceduralCar(this.opts.style || this.styleKey(), { color: this.item.color?.hex || '#888', stencil: true, sir: this.sir, hq: this.opts.quality.hq });
  }

  styleKey() { return this.opts.styleOf ? this.opts.styleOf(this.item) : 'sedan'; }

  // Frame the card: in the grid the camera fits the card into `fill` of the viewport height
  fit(aspect, fill = 0.8) {
    const cam = this.camera;
    cam.aspect = aspect;
    const half = THREE.MathUtils.degToRad(cam.fov / 2);
    cam.position.set(0, 0, (0.5 / fill) / Math.tan(half));
    cam.near = 0.05; cam.far = Math.max(cam.position.z + 5, 8); // the inspector backdrop sits up to 6.6 out
    cam.updateProjectionMatrix();
  }

  // Grid framing: the camera stays at a fixed distance and the field of view widens with the padded viewport, so the
  // card is the same size and the diorama keeps the same perspective however much padding a tilt needs.
  fitWindow(aspect, fill) {
    const cam = this.camera;
    cam.aspect = aspect;
    cam.fov = THREE.MathUtils.radToDeg(2 * Math.atan(0.5 / (fill * GRID_DIST)));
    cam.position.set(0, 0, GRID_DIST);
    cam.near = 0.05; cam.far = 8;
    cam.updateProjectionMatrix();
  }

  update(dt, t, { spin = true } = {}) {
    const s = this.s;
    const k = 170, c = 17;
    dt = Math.min(dt, 1 / 30);
    s.vrx += (k * (s.trx - s.rx) - c * s.vrx) * dt; s.rx += s.vrx * dt;
    s.vry += (k * (s.try - s.ry) - c * s.vry) * dt; s.ry += s.vry * dt;
    s.vlift += (220 * (s.tlift - s.lift) - 22 * s.vlift) * dt; s.lift += s.vlift * dt;
    this.root.rotation.set(s.rx, s.ry, 0);
    // the art follows only ~30% of the tilt: the window turns with the card, the scene behind it shifts a little
    // (depth, not a deep box you look down into). Measured from the nearest face-up angle so flips don't swing it.
    const fy = s.ry - Math.round(s.ry / (Math.PI * 2)) * Math.PI * 2;
    const follow = 0.7; // share of the tilt the art cancels
    this.dio.rotation.set(0.3 - THREE.MathUtils.clamp(s.rx, -0.8, 0.8) * follow, -THREE.MathUtils.clamp(fy, -0.8, 0.8) * follow, 0);
    this.root.position.z = s.lift * 0.35;
    this.root.scale.setScalar(1 + s.lift * 0.06);
    this.light.lerp(this.lightTarget, 1 - Math.exp(-dt * 10));
    for (const m of [this.face.material, this.foil.material]) { m.uniforms.uLight.value.copy(this.light); m.uniforms.uTime.value = t; }
    if (spin) this.spin += dt * 0.35;
    this.carHolder.rotation.y = this.full ? this.spin : -0.6 + Math.sin(this.spin) * 0.55;
    // hide the diorama when the back faces the camera (it lives behind the face)
    const n = new THREE.Vector3(0, 0, 1).applyQuaternion(this.root.quaternion);
    this.dio.visible = n.dot(this.camera.position.clone().sub(this.root.position).normalize()) > 0.02;
  }

  // 0 at rest .. 1 lifted / fully tilted: the grid pads the viewport by it and draws the most active card last
  activity() {
    const s = this.s;
    return Math.min(1, Math.max(Math.abs(s.lift), Math.abs(s.rx) / 0.6, Math.abs(s.ry) / 0.7));
  }

  setPointer(nx, ny, active) {
    // nx, ny in -1..1 across the card; tilt toward the pointer and pull the light along
    this.lightTarget.set(nx * 0.9, ny * 1.1, 1.3);
    if (active === 'hover') { this.s.trx = -ny * 0.16; this.s.try = nx * 0.2; }
  }

  dispose() {
    this.disposed = true;
    for (const d of this.disposables) d.dispose?.();
    this.scene.traverse((o) => { if (o.isLight && o.shadow?.map) o.shadow.map.dispose(); });
  }
}
