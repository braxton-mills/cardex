#include <metal_stdlib>
#include <SwiftUI/SwiftUI_Metal.h>
using namespace metal;

// Holographic foil for Cardex trading cards: a SwiftUI colorEffect drawn over a card region and blended on top.
// `tilt` is the card's tilt (-1...1 per axis); the light sits opposite it, so the foil shifts as the card turns.
// `style` picks the pattern (FoilStyle in TradingCardView.swift):
//   0 gloss  1 sheen  2 etched lines  3 swirl + glitter  4 cosmos  5 cracked ice  6 starlight  7 sequin
//   8 ripple  9 etched waves  10 etched hex  11 galaxy
// Output is premultiplied.

static half3 hue(float h) {
    float3 k = float3(1.0, 2.0 / 3.0, 1.0 / 3.0);
    float3 p = abs(fract(float3(h) + k) * 6.0 - 3.0);
    return half3(clamp(p - 1.0, 0.0, 1.0));
}

static float hash(float2 p) {
    return fract(sin(dot(p, float2(127.1, 311.7))) * 43758.5453);
}

static float2 hash2(float2 p) {
    return fract(sin(float2(dot(p, float2(127.1, 311.7)), dot(p, float2(269.5, 183.3)))) * 43758.5453);
}

static float noise(float2 p) {
    float2 i = floor(p), f = fract(p);
    float2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + float2(1, 0)), u.x), mix(hash(i + float2(0, 1)), hash(i + float2(1, 1)), u.x), u.y);
}

static float fbm(float2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) {
        v += a * noise(p);
        p *= 2.03;
        a *= 0.5;
    }
    return v;
}

/// Voronoi: x = distance to the nearest cell's border (0 on edges), yz = that cell's id.
static float3 voronoi(float2 p) {
    float2 n = floor(p), f = fract(p);
    float2 mg, mr;
    float md = 8.0;
    for (int j = -1; j <= 1; j++)
        for (int i = -1; i <= 1; i++) {
            float2 g = float2(i, j);
            float2 r = g + hash2(n + g) - f;
            float d = dot(r, r);
            if (d < md) { md = d; mr = r; mg = g; }
        }
    md = 8.0;
    for (int j = -2; j <= 2; j++)
        for (int i = -2; i <= 2; i++) {
            float2 g = mg + float2(i, j);
            float2 r = g + hash2(n + g) - f;
            if (dot(mr - r, mr - r) > 0.00001) md = min(md, dot(0.5 * (mr + r), normalize(r - mr)));
        }
    return float3(md, n + mg);
}

[[ stitchable ]] half4 foil(float2 position, half4 color, float2 size, float2 tilt, float style, float strength) {
    float2 uv = position / max(size, float2(1.0));
    float2 light = float2(0.5) - tilt * 0.75;
    float glow = 1.0 - smoothstep(0.0, 0.85, distance(uv, light));
    int s = int(style + 0.5);

    if (s == 0) {
        float a = glow * glow * 0.35 * strength;
        return half4(half3(a), a);
    }

    // the base rainbow: diagonal bands that slide with the tilt
    float phase = dot(uv, float2(0.8, 1.3)) * 2.2 + tilt.x * 0.9 - tilt.y * 0.6;
    half3 rainbow = hue(fract(phase));
    float a = (0.25 + 0.75 * glow) * 0.55;

    switch (s) {
    case 1: {   // sheen: fine grain so it reads as foil
        a *= 0.85 + 0.15 * hash(floor(position * 0.5));
        break;
    }
    case 2: {   // etched lines + faint cross-hatch
        float lines = smoothstep(0.55, 1.0, sin((uv.x + uv.y) * 260.0) * 0.5 + 0.5);
        float hatch = smoothstep(0.75, 1.0, sin((uv.x - uv.y) * 180.0) * 0.5 + 0.5);
        a *= 0.25 + 0.95 * max(lines, hatch * 0.6);
        break;
    }
    case 3: {   // swirl around the light, glitter
        float2 d = uv - light;
        float sw = atan2(d.y, d.x) / 6.2831 + length(d) * 1.6;
        rainbow = mix(rainbow, hue(fract(sw + tilt.x * 0.4)), 0.6h);
        float2 cell = floor(position / 6.0);
        float sparkle = step(0.94, hash(cell)) * (0.5 + 0.5 * sin(hash(cell + 7.0) * 40.0 + tilt.x * 9.0 + tilt.y * 7.0));
        a = a * 1.1 + sparkle * 0.9 * glow;
        break;
    }
    case 4: {   // cosmos: circles of foil of different sizes, each its own color
        float best = 0.0;
        float id = 0.0;
        for (int k = 0; k < 3; k++) {
            float scale = 16.0 + float(k) * 11.0;
            float2 p = position / scale + float(k) * 3.7;
            float2 cell = floor(p);
            // keep each circle inside its cell so none get cut off
            float r = 0.12 + 0.2 * hash(cell + 11.0);
            float2 c = r + hash2(cell) * (1.0 - 2.0 * r);
            float d = length(fract(p) - c);
            float inside = 1.0 - smoothstep(r - 0.05, r, d);
            float ring = smoothstep(r - 0.09, r - 0.03, d) * inside;
            float v = max(inside * 0.45, ring) * step(0.45, hash(cell + 3.0));
            if (v > best) { best = v; id = hash(cell + float(k)); }
        }
        rainbow = hue(fract(id * 0.35 + phase * 0.6));
        // the circles flash as the light passes over them
        a = (0.12 + best * 0.6) * (0.25 + 0.75 * glow);
        break;
    }
    case 5: {   // cracked ice: shards with bright edges
        float3 v = voronoi(position / 34.0);
        float edge = 1.0 - smoothstep(0.0, 0.06, v.x);
        rainbow = hue(fract(hash(v.yz) + tilt.x * 0.5 - tilt.y * 0.3));
        float facet = 0.4 + 0.6 * hash(v.yz + 5.0) * (0.5 + 0.5 * sin(tilt.x * 3.0 + hash(v.yz) * 6.28));
        a = (facet * 0.6 + edge * 0.8) * (0.3 + 0.7 * glow);
        break;
    }
    case 6: {   // starlight: dense twinkling stars over a soft rainbow
        float stars = 0.0;
        for (int k = 0; k < 2; k++) {
            float scale = 7.0 + float(k) * 5.0;
            float2 cell = floor(position / scale);
            float2 c = hash2(cell + float(k) * 9.0);
            float d = length(fract(position / scale) - c);
            float tw = 0.5 + 0.5 * sin(hash(cell) * 50.0 + tilt.x * 8.0 - tilt.y * 6.0);
            stars += (1.0 - smoothstep(0.0, 0.16, d)) * step(0.82, hash(cell + 2.0)) * tw;
        }
        a = a * 0.45 + stars * (0.5 + glow);
        rainbow = mix(rainbow, half3(1.0h), half(min(stars, 1.0) * 0.5));
        break;
    }
    case 7: {   // sequin: a hex grid of tiny discs
        float2 p = position / 7.0;
        float2 r = float2(1.0, 1.732);
        float2 h1 = fmod(p, r) - r * 0.5, h2 = fmod(p - r * 0.5, r) - r * 0.5;
        float2 g = dot(h1, h1) < dot(h2, h2) ? h1 : h2;
        float2 cell = p - g;
        float disc = 1.0 - smoothstep(0.32, 0.4, length(g));
        rainbow = hue(fract(phase + hash(floor(cell * 10.0)) * 0.25));
        float flash = 0.5 + 0.5 * sin(hash(floor(cell * 10.0)) * 30.0 + tilt.x * 6.0 + tilt.y * 4.0);
        a = disc * (0.25 + 0.75 * flash) * (0.35 + 0.65 * glow);
        break;
    }
    case 8: {   // ripple: rings from the art's center
        float d = length((uv - float2(0.5, 0.42)) * float2(size.x / size.y, 1.0));
        float rings = 0.5 + 0.5 * sin(d * 70.0 - tilt.x * 5.0 - tilt.y * 4.0);
        rainbow = hue(fract(d * 2.5 + tilt.x * 0.5));
        a = (0.2 + 0.8 * smoothstep(0.3, 1.0, rings)) * (0.3 + 0.7 * glow) * 0.7;
        break;
    }
    case 9: {   // etched waves: contour lines of a soft field
        float f = fbm(uv * 3.0 + 1.7) * 6.0 + uv.y * 3.0;
        float line = 1.0 - smoothstep(0.0, 0.12, abs(fract(f * 2.0) - 0.5) * 2.0 - 0.75);
        a *= 0.25 + 0.95 * smoothstep(0.0, 1.0, 1.0 - abs(fract(f * 2.0) - 0.5) * 4.0) * 0.8 + 0.2 * line;
        break;
    }
    case 10: {  // etched hex: honeycomb lines
        float2 p = position / 13.0;
        float2 r = float2(1.0, 1.732);
        float2 h1 = fmod(p, r) - r * 0.5, h2 = fmod(p - r * 0.5, r) - r * 0.5;
        float2 g = dot(h1, h1) < dot(h2, h2) ? h1 : h2;
        float2 q = abs(g);
        float hexd = max(dot(q, normalize(float2(1.0, 1.732))), q.x);
        float edge = smoothstep(0.42, 0.5, hexd);
        a *= 0.2 + 1.0 * edge;
        break;
    }
    case 11: {  // galaxy: nebula and stars
        float n = fbm(uv * 3.2 + tilt * 0.25);
        rainbow = hue(fract(n * 1.4 + tilt.x * 0.3 + 0.6));
        float2 cell = floor(position / 5.0);
        float star = step(0.965, hash(cell)) * (0.6 + 0.4 * sin(hash(cell + 4.0) * 30.0 + tilt.x * 7.0));
        a = (smoothstep(0.35, 0.8, n) * 0.8 + 0.1) * (0.35 + 0.65 * glow) + star;
        rainbow = mix(rainbow, half3(1.0h), half(star * 0.7));
        break;
    }
    default: break;
    }
    a = clamp(a * strength, 0.0, 1.0);
    return half4(rainbow * half(a), half(a));
}
