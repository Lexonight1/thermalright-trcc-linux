// KarmaViz warp map "Parallel Universe Bleed" (experimental): Multiple universe layers with different physics bleeding through dimensional barriers
// From KarmaViz 67e8a15 warp_maps/experimental/parallel_universe_bleed.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Parallel Universe Bleed
vec2 get_pattern(vec2 pos, float t) {
    // Multiple universe layers with different physics
    vec2 displacement = vec2(0.0);

    // Universe 1: Normal physics
    vec2 normal_warp = vec2(
        sin(pos.x * 8.0 + t * 1.0),
        cos(pos.y * 6.0 + t * 1.2)
    ) * 0.02;

    // Universe 2: Inverted gravity
    vec2 centered = pos - 0.5;
    vec2 inverted_warp = -centered * length(centered) * sin(t * 0.8) * 0.1;

    // Universe 3: Crystalline structure
    vec2 crystal_grid = pos * 12.0;
    vec2 crystal_cell = fract(crystal_grid);
    vec2 crystal_warp = (crystal_cell - 0.5) * sin(t * 1.5 + length(crystal_grid)) * 0.03;

    // Universe 4: Liquid dynamics
    vec2 liquid_warp = vec2(
        sin(pos.x * 15.0 + pos.y * 10.0 + t * 2.5),
        cos(pos.x * 12.0 - pos.y * 8.0 + t * 2.0)
    ) * 0.025;

    // Dimensional barriers (weak points where universes bleed through)
    float barrier1 = sin(pos.x * 20.0 + t * 0.5) * cos(pos.y * 18.0 + t * 0.7);
    float barrier2 = cos(pos.x * 16.0 - t * 0.6) * sin(pos.y * 14.0 - t * 0.4);
    float barrier3 = sin(pos.x * 22.0 + pos.y * 20.0 + t * 0.8);

    // Bleed-through effects
    float bleed1 = smoothstep(0.7, 1.0, abs(barrier1));
    float bleed2 = smoothstep(0.6, 1.0, abs(barrier2));
    float bleed3 = smoothstep(0.8, 1.0, abs(barrier3));

    // Combine universe effects based on bleed-through
    displacement += normal_warp * (1.0 - bleed1 - bleed2 - bleed3);
    displacement += inverted_warp * bleed1;
    displacement += crystal_warp * bleed2;
    displacement += liquid_warp * bleed3;

    // Reality distortion at bleed points
    float total_bleed = bleed1 + bleed2 + bleed3;
    displacement += vec2(sin(t * 5.0), cos(t * 4.0)) * total_bleed * 0.02;

    return displacement;
}
