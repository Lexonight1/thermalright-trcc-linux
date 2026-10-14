// KarmaViz warp map "Noise Flow" (experimental): Animated flow field using pseudo-random noise.
// From KarmaViz 67e8a15 warp_maps/experimental/noise_flow.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Noise Flow Warp Map
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
vec2 get_pattern(vec2 pos, float t) {
    float n = hash(floor(pos * 10.0) + t);
    float angle = n * 6.2831;
    return vec2(cos(angle), sin(angle)) * 0.018;
}
