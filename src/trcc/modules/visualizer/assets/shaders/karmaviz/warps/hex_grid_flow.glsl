// KarmaViz warp map "Hex Grid Flow" (experimental): Hexagonal grid with animated flow distortion.
// From KarmaViz 67e8a15 warp_maps/experimental/hex_grid_flow.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Hex Grid Flow Warp Map
vec2 get_pattern(vec2 pos, float t) {
    float qx = pos.x * 2.0 / sqrt(3.0);
    float qy = pos.y - pos.x / sqrt(3.0);
    float hex = mod(floor(qx + 0.5) + floor(qy + 0.5), 2.0);
    float flow = sin(t * 2.0 + pos.x * 8.0 + pos.y * 8.0) * 0.02;
    return vec2(flow * hex, -flow * (1.0 - hex));
}
