// KarmaViz warp map "Wave Interference" (experimental): Interference pattern from two moving wave sources.
// From KarmaViz 67e8a15 warp_maps/experimental/wave_interference.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Wave Interference Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 src1 = vec2(0.3 + 0.1 * sin(t), 0.5);
    vec2 src2 = vec2(0.7 + 0.1 * cos(t), 0.5);
    float w1 = sin(30.0 * length(pos - src1) - t * 2.0);
    float w2 = sin(30.0 * length(pos - src2) + t * 2.0);
    float interference = (w1 + w2) * 0.012;
    return normalize(pos - 0.5) * interference;
}
