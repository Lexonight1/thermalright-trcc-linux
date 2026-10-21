// KarmaViz warp map "Deppth of Field" (basic): Simple blurringt of the edges, simulating photographic depth of field
// From KarmaViz 67e8a15 warp_maps/basic/deppth_of_field.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Radial Pulse Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    float dist = length(pos - center);
    float pulse = sin(100.0 * dist * t * 2.0) * 0.025 * smoothstep(0.3, 0.5, dist);
    return normalize(pos - center) * pulse;
}
