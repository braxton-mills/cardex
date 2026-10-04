#include <metal_stdlib>
#include <SwiftUI/SwiftUI_Metal.h>
using namespace metal;

// Holographic foil for Cardex trading cards: a SwiftUI colorEffect drawn over a card region and blended on top.
// `tilt` is the card's tilt (-1...1 per axis); the light sits opposite it, so the bands slide as the card turns.
// style: 0 gloss (plain), 1 rainbow bands (reverse holo, holo), 2 etched lines (full art), 3 rainbow swirl with
// glitter (special illustration rare). Output is premultiplied.

static half3 hue(float h) {
    float3 k = float3(1.0, 2.0 / 3.0, 1.0 / 3.0);
    float3 p = abs(fract(float3(h) + k) * 6.0 - 3.0);
    return half3(clamp(p - 1.0, 0.0, 1.0));
}

static float hash(float2 p) {
    return fract(sin(dot(p, float2(127.1, 311.7))) * 43758.5453);
}

[[ stitchable ]] half4 foil(float2 position, half4 color, float2 size, float2 tilt, float style, float strength) {
    float2 uv = position / max(size, float2(1.0));
    float2 light = float2(0.5) - tilt * 0.75;
    float glow = 1.0 - smoothstep(0.0, 0.85, distance(uv, light));

    if (style < 0.5) {
        // gloss: a soft sheen only where the light is
        float a = glow * glow * 0.35 * strength;
        return half4(half3(a), a);
    }

    // bands run diagonally and slide with the tilt
    float phase = dot(uv, float2(0.8, 1.3)) * 2.2 + tilt.x * 0.9 - tilt.y * 0.6;
    half3 rainbow = hue(fract(phase));
    float a = (0.25 + 0.75 * glow) * 0.55;

    if (style < 1.5) {
        // fine grain so it reads as foil, not a gradient
        float grain = 0.85 + 0.15 * hash(floor(position * 0.5));
        a *= grain;
    } else if (style < 2.5) {
        // etched: thin diagonal lines that catch the light, plus a faint cross-hatch
        float lines = smoothstep(0.55, 1.0, sin((uv.x + uv.y) * 260.0) * 0.5 + 0.5);
        float hatch = smoothstep(0.75, 1.0, sin((uv.x - uv.y) * 180.0) * 0.5 + 0.5);
        a *= 0.25 + 0.95 * max(lines, hatch * 0.6);
    } else {
        // swirl: hue bent around the light, with glitter that twinkles as the card moves
        float2 d = uv - light;
        float swirl = atan2(d.y, d.x) / 6.2831 + length(d) * 1.6;
        rainbow = mix(rainbow, hue(fract(swirl + tilt.x * 0.4)), 0.6h);
        float2 cell = floor(position / 6.0);
        float sparkle = step(0.94, hash(cell)) * (0.5 + 0.5 * sin(hash(cell + 7.0) * 40.0 + tilt.x * 9.0 + tilt.y * 7.0));
        a = a * 1.1 + sparkle * 0.9 * glow;
    }
    a = clamp(a * strength, 0.0, 1.0);
    return half4(rainbow * half(a), half(a));
}
