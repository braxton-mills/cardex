// Card surface shaders. The face shader draws the printed card (alpha-blended over the 3D art, discarded where the
// art window is clear) with a clearcoat glint. The foil shader is a second, additive layer on the same plane that
// does the finish: its colour comes from the angle between the card, the viewer and the light, so tilting the card
// (dragging it) moves the rainbow bands, flashes the glitter and slides the gloss across the face.
import * as THREE from 'three';

export const FINISH_ID = { plain: 0, reverse: 1, holo: 2, fullart: 3, sir: 4, none: 5 };

const VERT = /* glsl */`
varying vec2 vUv;
varying vec3 vWorldPos;
varying vec3 vNormal;
varying vec3 vTangent;
varying vec3 vBitangent;
void main() {
  vUv = uv;
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz;
  mat3 m = mat3(modelMatrix);
  vNormal = normalize(m * vec3(0.0, 0.0, 1.0));
  vTangent = normalize(m * vec3(1.0, 0.0, 0.0));
  vBitangent = normalize(m * vec3(0.0, 1.0, 0.0));
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const COMMON = /* glsl */`
varying vec2 vUv;
varying vec3 vWorldPos;
varying vec3 vNormal;
varying vec3 vTangent;
varying vec3 vBitangent;
uniform vec3 uLight;
uniform float uTime;
vec3 hsv2rgb(vec3 c) {
  vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
  return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
  return v;
}`;

export function faceMaterial(faceTex) {
  return new THREE.ShaderMaterial({
    uniforms: { map: { value: faceTex }, uLight: { value: new THREE.Vector3(0, 0.6, 1.5) }, uTime: { value: 0 } },
    vertexShader: VERT,
    fragmentShader: /* glsl */`
${COMMON}
uniform sampler2D map;
void main() {
  vec4 c = texture2D(map, vUv);
  if (c.a < 0.015) discard;
  vec3 V = normalize(cameraPosition - vWorldPos);
  vec3 N = normalize(vNormal);
  vec3 L = normalize(uLight - vWorldPos);
  vec3 H = normalize(L + V);
  float ndh = max(dot(N, H), 0.0);
  float spec = pow(ndh, 900.0) * 0.45 + pow(ndh, 60.0) * 0.035;
  float fres = pow(1.0 - max(dot(N, V), 0.0), 5.0) * 0.18;
  float shade = 0.82 + 0.18 * max(dot(N, L), 0.0);
  gl_FragColor = vec4(c.rgb * shade + (spec + fres) * c.a, c.a);
  #include <colorspace_fragment>
}`,
    transparent: true,
    depthWrite: true,
  });
}

export function foilMaterial(maskTex, finish) {
  return new THREE.ShaderMaterial({
    uniforms: {
      mask: { value: maskTex }, uLight: { value: new THREE.Vector3(0, 0.6, 1.5) }, uTime: { value: 0 },
      uFinish: { value: FINISH_ID[finish] ?? 0 }, uStrength: { value: 1 },
    },
    vertexShader: VERT,
    fragmentShader: /* glsl */`
${COMMON}
uniform sampler2D mask;
uniform int uFinish;
uniform float uStrength;
void main() {
  vec4 mk = texture2D(mask, vUv);
  float art = mk.r, allow = mk.g, border = mk.b;
  vec3 V = normalize(cameraPosition - vWorldPos);
  vec3 N = normalize(vNormal);
  vec3 T = normalize(vTangent), B = normalize(vBitangent);
  vec3 L = normalize(uLight - vWorldPos);
  vec3 H = normalize(L + V);
  vec2 vt = vec2(dot(V, T), dot(V, B));      // where the viewer is, in the card's own frame
  vec2 ht = vec2(dot(H, T), dot(H, B));
  float ndh = max(dot(N, H), 0.0);
  // diffraction: hue follows position on the card plus viewing angle, so tilting sweeps the rainbow
  float phase = dot(vUv - 0.5, vec2(0.85, 1.25)) * 1.9 + vt.x * 2.8 + vt.y * 2.1;
  vec3 rainbow = hsv2rgb(vec3(fract(phase), 0.72, 1.0));
  // a soft band where the light reflects toward the viewer; it slides across as the card turns
  float band = exp(-pow(length((vUv - 0.5) * vec2(0.72, 1.0) - ht * 0.9), 2.0) * 9.0);
  float glare = pow(ndh, 260.0);
  vec3 col = vec3(0.0);

  if (uFinish == 0) {                       // plain: clearcoat glint only
    col = vec3(glare * 0.5);
  } else if (uFinish == 1) {                // reverse holo: foil everywhere except the art window
    float region = (1.0 - art) * allow * (1.0 - border * 0.7);
    vec2 cell = floor(vUv * vec2(240.0, 335.0));
    vec3 fn = normalize(vec3(hash(cell) - 0.5, hash(cell + 3.1) - 0.5, 2.2));
    float fleck = pow(max(dot(normalize(fn.x * T + fn.y * B + fn.z * N), H), 0.0), 320.0) * step(0.85, hash(cell + 9.7));
    col = (rainbow * (0.12 + 0.9 * band) + vec3(1.0) * fleck * 0.45) * region;
  } else if (uFinish == 2) {                // holo rare: "cosmos" foil inside the art window
    float region = art * allow;
    float swirl = fbm(vUv * vec2(5.0, 7.0) + vt * 0.7);
    float neb = fbm(vUv * 2.6 + vec2(swirl * 1.3, -swirl));
    vec2 sc = floor(vUv * vec2(170.0, 237.0));
    float star = step(0.993, hash(sc)) * (0.5 + 0.5 * sin(uTime * 3.0 + hash(sc + 1.0) * 40.0));
    float twinkle = star * pow(ndh, 24.0) * 0.9;
    vec3 c = hsv2rgb(vec3(fract(phase + neb * 0.7), 0.62, 1.0));
    col = (c * (0.08 + 0.95 * band) * (0.35 + neb * 1.1) + vec3(twinkle)) * region;
    col += border * rainbow * 0.08 * band;
  } else if (uFinish == 3) {                // full art: etched diagonal foil over the whole card
    float l = abs(fract((vUv.x * 0.8 + vUv.y * 1.12) * 95.0) - 0.5);
    float lines = smoothstep(0.22, 0.42, l);
    float tex = fbm(vUv * vec2(18.0, 25.0));
    col = rainbow * (0.06 + 0.85 * band) * lines * (0.6 + tex * 0.7) * allow;
    col += border * mix(vec3(0.92, 0.95, 1.0), rainbow, 0.45) * (0.18 + 1.3 * band);
    col += vec3(glare) * 0.7;
  } else if (uFinish == 4) {                // special illustration rare: glitter + gold sweep + textured emboss
    vec2 cell = floor(vUv * vec2(160.0, 223.0));
    vec3 fn = normalize(vec3(hash(cell) - 0.5, hash(cell + 7.1) - 0.5, 1.4));
    vec3 Nf = normalize(fn.x * T + fn.y * B + fn.z * N);
    float glit = pow(max(dot(Nf, H), 0.0), 600.0) * 1.1 * step(0.95, hash(cell + 2.3));
    float tex = fbm(vUv * vec2(36.0, 50.0) + vec2(uTime * 0.01));
    vec3 gold = mix(vec3(1.0, 0.78, 0.32), rainbow, 0.5 + 0.3 * sin(phase * 3.0));
    col = gold * (0.02 + 0.3 * band) * (0.35 + tex) * allow * (1.0 - art * 0.6);
    col += vec3(1.0, 0.95, 0.82) * glit * allow * (1.0 - art * 0.85);
    col += border * vec3(1.0, 0.83, 0.42) * (0.25 + 1.0 * band);
  } else {                                  // not caught yet: dull
    col = vec3(glare * 0.2);
  }
  float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);
  col += fres * rainbow * (uFinish >= 2 && uFinish <= 4 ? 0.2 : 0.06);
  gl_FragColor = vec4(col * uStrength, 1.0);
}`,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
}

// Floating sparkles around the card in the inspector (additive, twinkling)
export function sparkleMaterial(color) {
  return new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(color) }, uScale: { value: 1 } },
    vertexShader: /* glsl */`
attribute float seed;
uniform float uTime;
uniform float uScale;
varying float vA;
void main() {
  vec3 p = position;
  p.y += mod(uTime * (0.03 + seed * 0.05) + seed * 3.0, 2.4) - 1.2;
  p.x += sin(uTime * 0.6 + seed * 20.0) * 0.03;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vA = pow(0.5 + 0.5 * sin(uTime * (2.0 + seed * 3.0) + seed * 50.0), 6.0);
  gl_PointSize = uScale * (4.0 + seed * 9.0) / -mv.z;
  gl_Position = projectionMatrix * mv;
}`,
    fragmentShader: /* glsl */`
uniform vec3 uColor;
varying float vA;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  float r = length(d);
  float cross = max(0.0, 1.0 - abs(d.x) * 9.0) * max(0.0, 1.0 - abs(d.y) * 1.6) + max(0.0, 1.0 - abs(d.y) * 9.0) * max(0.0, 1.0 - abs(d.x) * 1.6);
  float a = (smoothstep(0.5, 0.0, r) * 0.6 + cross) * vA;
  gl_FragColor = vec4(uColor * a * 0.9, 1.0);
}`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
}
