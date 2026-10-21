// KarmaViz warp map "Radial Pulse" (experimental): Pulsing radial waves from the center.
// From KarmaViz 67e8a15 warp_maps/experimental/radial_pulse.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Radial Pulse Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    float dist = length(pos - center);
    float pulse = sin(20.0 * dist - t * 4.0) * 0.025 * smoothstep(0.1, 0.5, dist);
    return normalize(pos - center) * pulse;
}
