// KarmaViz warp map "Twist Grid" (experimental): Grid pattern with a twisting animation.
// From KarmaViz 67e8a15 warp_maps/experimental/twist_grid.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Twist Grid Warp Map
vec2 get_pattern(vec2 pos, float t) {
    float grid = step(0.5, mod(floor(pos.x * 12.0) + floor(pos.y * 12.0), 2.0));
    float twist = sin((pos.x - pos.y) * 12.0 + t * 3.0) * 0.018;
    return vec2(twist * grid, twist * (1.0 - grid));
}
