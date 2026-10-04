// Cards renderer: one WebGL2 context for the whole tab.
// Grid: a fixed, click-through canvas over the page; each visible .tcard placeholder gets its own viewport + scissor
// (clipped to #main) and its card is rendered into it, so layout, scrolling and hit-testing stay plain DOM and only
// on-screen cards cost anything. Inspector: the same context renders one big card through a composer (HDR target,
// bloom, tone mapping, supersampled) with sparkles; drag to tilt or flip it.
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Card3D } from './card3d.js';
import { sparkleMaterial } from './shaders.js';
import { bodyStyle } from './styles.js';

const dpr = () => window.devicePixelRatio || 1;
// Anti-aliasing is supersampling (render at a multiple of the device pixel ratio): on ANGLE/D3D11 a multisampled
// half-float + stencil target costs ~9x more than rendering 9x the pixels, so the composer never uses MSAA.
export const PRESETS = {
  ultra: { label: 'Ultra', gridDpr: () => Math.min(dpr() * 1.75, 3), inspDpr: () => Math.min(dpr() * 2.5, 4), bloom: true, shadows: true, shadowSize: 4096, hq: true, facePx: 900, inspFacePx: 2048, sparkles: 1400, builds: 4 },
  high: { label: 'High', gridDpr: () => Math.min(dpr() * 1.25, 2), inspDpr: () => Math.min(dpr() * 1.75, 3), bloom: true, shadows: true, shadowSize: 2048, hq: true, facePx: 750, inspFacePx: 1500, sparkles: 700, builds: 3 },
  low: { label: 'Low', gridDpr: () => 1, inspDpr: () => 1, bloom: false, shadows: false, shadowSize: 1024, hq: false, facePx: 600, inspFacePx: 1024, sparkles: 250, builds: 2 },
};
const SPARKLE = { sir: '#ffd36b', fullart: '#d8e8ff', holo: '#ffffff', reverse: '#c4f1ff', plain: '#ffffff', none: '#000000' };
const SPARKLE_N = { sir: 1, fullart: 0.85, holo: 0.6, reverse: 0.4, plain: 0.2, none: 0 };

export function gpuName(renderer) {
  const gl = renderer.getContext();
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const raw = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const m = /(RTX|GTX|RX|Arc)\s*[A-Z]?\d{3,4}\s*\w*/i.exec(raw || '') || /(UHD|Iris)[^,(]*/i.exec(raw || '');
  return { raw: raw || 'unknown', short: m ? m[0].replace(/\s+(Laptop|Ti\b)?\s*$/i, (s) => s).trim() : 'WebGL' };
}

export function autoPreset(raw) {
  if (/swiftshader|llvmpipe|software|basic render/i.test(raw)) return 'low';
  if (/nvidia|geforce|rtx|radeon rx|arc a/i.test(raw)) return 'ultra';
  return 'high';
}

export class CardEngine {
  constructor({ main, preset = null, reduceMotion = () => false, onPresetChange = null }) {
    this.main = main;
    this.reduceMotion = reduceMotion;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'cards-gl';
    document.body.append(this.canvas);
    const r = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true, stencil: true, powerPreference: 'high-performance', premultipliedAlpha: true });
    if (!r.capabilities.isWebGL2) throw new Error('WebGL2 is not available');
    this.renderer = r;
    r.toneMapping = THREE.NeutralToneMapping;
    r.toneMappingExposure = 1.0;
    r.outputColorSpace = THREE.SRGBColorSpace;
    // render() must not clear: each card's padded viewport overlaps its neighbours, and an auto-clear would erase them
    // (the grid clears depth + stencil per card by hand; the composer's RenderPass clears its own target)
    r.autoClear = false;
    r.shadowMap.type = THREE.PCFShadowMap;
    r.info.autoReset = false;
    this.gpu = gpuName(r);
    this.presetKey = preset && PRESETS[preset] ? preset : autoPreset(this.gpu.raw);
    this.onPresetChange = onPresetChange;
    const pm = new THREE.PMREMGenerator(r);
    this.env = pm.fromScene(new RoomEnvironment(), 0.035).texture;
    pm.dispose();

    this.cards = new Map();       // el -> {item, card, lastSeen}
    this.visible = new Set();
    this.pending = [];
    this.io = new IntersectionObserver((entries) => {
      for (const e of entries) { if (e.isIntersecting) this.visible.add(e.target); else this.visible.delete(e.target); }
    }, { root: main, rootMargin: '120px 0px' });
    this.timer = new THREE.Timer();
    this.t = 0;
    this.running = false;
    this.hover = null;
    this.drag = null;
    this.size = { w: 0, h: 0, pr: 0 };
    this.fps = { frames: 0, last: performance.now(), value: 0 };
    this.frame = this.frame.bind(this);
    this.resume();
  }

  get preset() { return PRESETS[this.presetKey]; }

  setPreset(key) {
    if (!PRESETS[key] || key === this.presetKey) return;
    this.presetKey = key;
    for (const [, rec] of this.cards) { rec.card?.dispose(); rec.card = null; }
    this.onPresetChange?.(key);
    if (this.insp) { const { item } = this.insp; this.closeInspector(); this.openInspector(item); }
  }

  quality(insp) {
    const p = this.preset;
    return { hq: p.hq, shadows: insp && p.shadows, shadowSize: p.shadowSize };
  }

  // ---------------------------------------------------------------- grid

  register(el, item) {
    this.cards.set(el, { item, card: null, lastSeen: 0, phase: Math.random() * 10 });
    this.io.observe(el);
  }

  clear() {
    for (const [el, rec] of this.cards) { this.io.unobserve(el); rec.card?.dispose(); }
    this.cards.clear();
    this.visible.clear();
  }

  cardFor(el) { return this.cards.get(el)?.card || null; }

  build(rec) {
    rec.card = new Card3D(rec.item, {
      renderer: this.renderer, env: this.env, quality: this.quality(false), gpu: this.gpu.short, total: this.total,
      px: this.preset.facePx, styleOf: bodyStyle, scanned: this.scanned,
    });
  }

  // pointer handling for a grid (delegated); onOpen(item) on a tap/click without a drag
  attachGrid(grid, onOpen) {
    const pos = (el, e) => {
      const r = el.getBoundingClientRect();
      return [((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1), r];
    };
    const down = (e) => {
      const el = e.target.closest('.tcard');
      if (!el || e.button > 0) return;
      const card = this.cardFor(el);
      this.drag = { el, id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), moved: 0 };
      try { el.setPointerCapture(e.pointerId); } catch { /* ignore */ }
      if (card) card.s.tlift = 1;
      el.classList.add('lifted');
    };
    const move = (e) => {
      const d = this.drag;
      if (d && e.pointerId === d.id) {
        const card = this.cardFor(d.el);
        const [nx, ny, r] = pos(d.el, e);
        d.moved = Math.max(d.moved, Math.hypot(e.clientX - d.x0, e.clientY - d.y0));
        if (card) {
          // held like a real card: up to ~35-40 degrees
          card.s.trx = THREE.MathUtils.clamp(-((e.clientY - d.y0) / r.height) * 2.2, -0.6, 0.6);
          card.s.try = THREE.MathUtils.clamp(((e.clientX - d.x0) / r.width) * 2.2, -0.7, 0.7);
          card.lightTarget.set(nx * 1.2, ny * 1.4, 1.2);
        }
        return;
      }
      if (e.pointerType === 'touch') return;
      const el = e.target.closest?.('.tcard');
      if (el !== this.hover) { this.unhover(); this.hover = el; }
      if (el) { const [nx, ny] = pos(el, e); this.cardFor(el)?.setPointer(nx, ny, 'hover'); }
    };
    const up = (e) => {
      const d = this.drag;
      if (!d || e.pointerId !== d.id) return;
      this.drag = null;
      d.el.classList.remove('lifted');
      const card = this.cardFor(d.el);
      if (card) { card.s.tlift = 0; card.s.trx = 0; card.s.try = 0; }
      if (e.type === 'pointerup' && d.moved < 7 && performance.now() - d.t0 < 500) onOpen(this.cards.get(d.el)?.item, d.el);
    };
    grid.addEventListener('pointerdown', down);
    grid.addEventListener('pointermove', move);
    grid.addEventListener('pointerup', up);
    grid.addEventListener('pointercancel', up);
    grid.addEventListener('pointerleave', () => this.unhover());
    this.detachGrid = () => {
      grid.removeEventListener('pointerdown', down); grid.removeEventListener('pointermove', move);
      grid.removeEventListener('pointerup', up); grid.removeEventListener('pointercancel', up);
    };
  }

  unhover() {
    const c = this.hover && this.cardFor(this.hover);
    if (c && this.drag?.el !== this.hover) { c.s.trx = 0; c.s.try = 0; }
    this.hover = null;
  }

  resize(pr) {
    const w = window.innerWidth, h = window.innerHeight;
    if (w === this.size.w && h === this.size.h && pr === this.size.pr) return false;
    this.size = { w, h, pr };
    this.renderer.setPixelRatio(pr);
    this.renderer.setSize(w, h, false);
    return true;
  }

  renderGrid(dt) {
    const r = this.renderer;
    this.resize(this.preset.gridDpr());
    r.setScissorTest(false);
    r.setClearColor(0x000000, 0);
    r.clear(true, true, true);
    const mr = this.main.getBoundingClientRect();
    const H = this.size.h;
    // build a few missing cards per frame, nearest first
    let builds = this.preset.builds;
    const list = [...this.visible].map((el) => [el, el.getBoundingClientRect()]).filter(([, b]) => b.bottom > mr.top - 40 && b.top < mr.bottom + 40);
    list.sort((a, b) => a[1].top - b[1].top || a[1].left - b[1].left);
    // the most lifted/tilted card draws last so it overlaps its neighbours (also while it springs back after release)
    const lifted = this.drag?.el;
    const act = (el) => (el === lifted ? 2 : this.cards.get(el)?.card?.activity() || 0);
    list.sort((a, b) => act(a[0]) - act(b[0]));
    r.setScissorTest(true);
    for (const [el, b] of list) {
      const rec = this.cards.get(el);
      if (!rec) continue;
      if (!rec.card) {
        if (builds-- <= 0) continue;
        this.build(rec);
        el.classList.add('gl-ready');
      }
      rec.lastSeen = this.t;
      const card = rec.card;
      const idle = el !== this.hover && el !== lifted && !this.reduceMotion();
      if (idle) {
        card.s.trx = Math.sin(this.t * 0.7 + rec.phase) * 0.05;
        card.s.try = Math.cos(this.t * 0.5 + rec.phase) * 0.07;
        card.lightTarget.set(Math.sin(this.t * 0.4 + rec.phase) * 0.8, 0.7 + Math.cos(this.t * 0.3 + rec.phase) * 0.4, 1.4);
      }
      card.update(dt, this.t, { spin: !this.reduceMotion() });
      const pad = b.width * (0.14 + 0.4 * (el === lifted ? 1 : card.activity()));
      const vx = b.left - pad, vy = H - b.bottom - pad, vw = b.width + pad * 2, vh = b.height + pad * 2;
      const sx = Math.max(vx, mr.left), sy = Math.max(vy, H - mr.bottom);
      const ex = Math.min(vx + vw, mr.right), ey = Math.min(vy + vh, H - mr.top);
      if (ex <= sx || ey <= sy) continue;
      r.setViewport(vx, vy, vw, vh);
      r.setScissor(sx, sy, ex - sx, ey - sy);
      r.clear(false, true, true);
      card.fit(vw / vh, b.height / vh);
      r.render(card.scene, card.camera);
    }
    r.setScissorTest(false);
    // keep GPU memory bounded: drop cards that have been off screen for a while
    if (this.cards.size > 140 && Math.floor(this.t) % 5 === 0) {
      for (const [, rec] of this.cards) if (rec.card && this.t - rec.lastSeen > 20) { rec.card.dispose(); rec.card = null; }
    }
  }

  // ---------------------------------------------------------------- inspector

  openInspector(item, { scanned = this.scanned } = {}) {
    this.closeInspector();
    const p = this.preset;
    const card = new Card3D(item, {
      renderer: this.renderer, env: this.env, quality: this.quality(true), gpu: this.gpu.short, total: this.total,
      px: p.inspFacePx, styleOf: bodyStyle, scanned,
    });
    card.camera.fov = 30;
    const scene = card.scene;
    const bg = document.createElement('canvas');
    bg.width = bg.height = 512;
    const g = bg.getContext('2d');
    const grd = g.createRadialGradient(256, 230, 10, 256, 256, 380);
    grd.addColorStop(0, card.finish === 'none' ? '#1a1c26' : `${card.type.deep}55`);
    grd.addColorStop(0.45, '#0c0d1a'); grd.addColorStop(1, '#04040a');
    g.fillStyle = '#05050b'; g.fillRect(0, 0, 512, 512); g.fillStyle = grd; g.fillRect(0, 0, 512, 512);
    const bgT = new THREE.CanvasTexture(bg); bgT.colorSpace = THREE.SRGBColorSpace;
    scene.background = bgT;
    const n = Math.round(p.sparkles * (SPARKLE_N[card.finish] ?? 0.3));
    let sparks = null;
    if (n) {
      const geo = new THREE.BufferGeometry();
      const pos = new Float32Array(n * 3), seed = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        pos[i * 3] = (Math.random() - 0.5) * 1.9; pos[i * 3 + 1] = (Math.random() - 0.5) * 1.6; pos[i * 3 + 2] = -0.4 + Math.random() * 0.9;
        seed[i] = Math.random();
      }
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geo.setAttribute('seed', new THREE.BufferAttribute(seed, 1));
      sparks = new THREE.Points(geo, sparkleMaterial(SPARKLE[card.finish]));
      sparks.renderOrder = 5;
      scene.add(sparks);
    }
    this.renderer.shadowMap.enabled = !!p.shadows;
    const pr = p.inspDpr();
    this.resize(pr);
    const w = this.size.w, h = this.size.h;
    const rt = new THREE.WebGLRenderTarget(w * pr, h * pr, { type: THREE.HalfFloatType, stencilBuffer: true, depthBuffer: true });
    const composer = new EffectComposer(this.renderer, rt);
    composer.setPixelRatio(pr);
    composer.setSize(w, h);
    composer.addPass(new RenderPass(scene, card.camera));
    let bloom = null;
    if (p.bloom) { bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.42, 0.38, 1.0); composer.addPass(bloom); }
    composer.addPass(new OutputPass());
    this.insp = { item, card, composer, sparks, bgT, zoom: 0.8, auto: !this.reduceMotion(), size: `${w}x${h}x${pr}` };
    card.s.ry = -Math.PI * 0.9; card.s.lift = -0.6; // fly in, flipping from the back
    document.body.classList.add('cards-inspecting');
    return card;
  }

  closeInspector() {
    const ins = this.insp;
    if (!ins) return;
    this.insp = null;
    ins.composer.renderTarget1.dispose(); ins.composer.renderTarget2.dispose();
    for (const pass of ins.composer.passes) pass.dispose?.();
    ins.sparks?.geometry.dispose(); ins.sparks?.material.dispose();
    ins.bgT.dispose();
    ins.card.dispose();
    this.renderer.shadowMap.enabled = false;
    document.body.classList.remove('cards-inspecting');
  }

  // drag / flip / zoom in the inspector (pointer events from the overlay element)
  attachInspector(el) {
    let d = null;
    const down = (e) => {
      if (!this.insp || e.target.closest('button, a, select')) return;
      const s = this.insp.card.s;
      d = { id: e.pointerId, x0: e.clientX, y0: e.clientY, ry: s.try, rx: s.trx };
      el.setPointerCapture(e.pointerId);
      this.insp.dragging = true;
    };
    const move = (e) => {
      if (!this.insp) return;
      const nx = (e.clientX / window.innerWidth) * 2 - 1, ny = -((e.clientY / window.innerHeight) * 2 - 1);
      this.insp.card.lightTarget.set(nx * 1.6, ny * 1.6, 1.5);
      if (!d || e.pointerId !== d.id) return;
      const s = this.insp.card.s;
      s.try = d.ry + (e.clientX - d.x0) * 0.0085;
      s.trx = THREE.MathUtils.clamp(d.rx + (e.clientY - d.y0) * 0.0065, -1.1, 1.1);
    };
    const up = (e) => {
      if (!d || e.pointerId !== d.id || !this.insp) return;
      d = null;
      this.insp.dragging = false;
      const s = this.insp.card.s;
      s.try = Math.round(s.try / Math.PI) * Math.PI; // settle on the front or the back
      s.trx = 0;
    };
    const wheel = (e) => {
      if (!this.insp) return;
      e.preventDefault();
      this.insp.zoom = THREE.MathUtils.clamp(this.insp.zoom * (e.deltaY > 0 ? 0.92 : 1.08), 0.45, 1.25);
    };
    const dbl = () => this.flip();
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', wheel, { passive: false });
    el.addEventListener('dblclick', dbl);
  }

  flip() { if (this.insp) this.insp.card.s.try = Math.round(this.insp.card.s.try / Math.PI) * Math.PI + Math.PI; }

  renderInspector(dt) {
    const ins = this.insp;
    const p = this.preset;
    const pr = p.inspDpr();
    if (this.resize(pr) || ins.size !== `${this.size.w}x${this.size.h}x${pr}`) {
      ins.composer.setPixelRatio(pr);
      ins.composer.setSize(this.size.w, this.size.h);
      ins.size = `${this.size.w}x${this.size.h}x${pr}`;
    }
    const card = ins.card;
    if (!ins.dragging && ins.auto) {
      card.s.trx = Math.sin(this.t * 0.6) * 0.12;
      card.s.try = Math.round(card.s.try / Math.PI) * Math.PI + Math.sin(this.t * 0.45) * 0.28;
    }
    card.s.tlift = 0;
    card.update(dt, this.t, { spin: !this.reduceMotion() });
    card.root.position.y = Math.sin(this.t * 1.1) * 0.012;
    card.fit(this.size.w / this.size.h, ins.zoom);
    if (ins.sparks) ins.sparks.material.uniforms.uTime.value = this.t, ins.sparks.material.uniforms.uScale.value = this.size.h * pr * 0.0022;
    this.renderer.setViewport(0, 0, this.size.w, this.size.h);
    ins.composer.render(dt);
  }

  // ---------------------------------------------------------------- loop

  frame(ts) {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.frame);
    this.timer.update(ts);
    const dt = Math.min(this.timer.getDelta(), 0.1);
    this.t += dt;
    this.renderer.info.reset();
    if (this.insp) this.renderInspector(dt); else this.renderGrid(dt);
    const f = this.fps;
    f.frames++;
    const now = performance.now();
    if (now - f.last >= 500) {
      f.value = (f.frames * 1000) / (now - f.last); f.frames = 0; f.last = now;
      this.onStats?.(this.stats());
    }
  }

  stats() {
    const i = this.renderer.info;
    return {
      gpu: this.gpu.raw, preset: this.preset.label, fps: this.fps.value, calls: i.render.calls, triangles: i.render.triangles,
      cards: [...this.cards.values()].filter((c) => c.card).length, visible: this.visible.size,
      px: `${Math.round(this.size.w * this.size.pr)}×${Math.round(this.size.h * this.size.pr)}`, mode: this.insp ? 'inspect' : 'grid',
      textures: i.memory.textures, geometries: i.memory.geometries,
    };
  }

  pause() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  resume() {
    if (this.running || this.disposed) return;
    this.running = true;
    this.timer.reset?.();
    this.raf = requestAnimationFrame(this.frame);
  }

  dispose() {
    this.pause();
    this.disposed = true;
    this.detachGrid?.();
    this.closeInspector();
    this.io.disconnect();
    this.clear();
    this.env.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.remove();
  }
}
