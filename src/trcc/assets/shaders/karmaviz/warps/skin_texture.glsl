// KarmaViz warp map "Skin Texture" (organic): Realistic skin texture with pores, elasticity, breathing motion, and fine wrinkle patterns
// From KarmaViz 67e8a15 warp_maps/organic/skin_texture.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Skin Texture
vec2 get_pattern(vec2 pos, float t) {
    // Create skin pore pattern
    vec2 pore_grid = pos * 20.0;
    vec2 pore_id = floor(pore_grid);
    vec2 pore_local = fract(pore_grid);

    // Pseudo-random pore positions
    vec2 pore_offset = vec2(
        sin(pore_id.x * 12.9898 + pore_id.y * 78.233) * 0.3,
        cos(pore_id.x * 93.9898 + pore_id.y * 67.345) * 0.3
    );

    vec2 pore_center = pore_local + pore_offset;
    float pore_dist = length(pore_center - 0.5);

    // Skin elasticity and breathing
    float breathing = sin(t * 1.2) * 0.02 + 1.0;
    float elasticity = sin(pos.x * 15.0 + pos.y * 12.0 + t * 0.8) * 0.01;

    vec2 displacement = vec2(0.0);

    // Pore displacement
    if (pore_dist < 0.3) {
        vec2 to_pore = normalize(pore_center - 0.5);
        displacement += to_pore * (0.3 - pore_dist) * 0.005;
    }

    // Overall skin movement
    displacement += vec2(elasticity, elasticity * 0.7) * breathing;

    // Add fine wrinkle patterns
    float wrinkle = sin(pos.x * 25.0 + t * 0.5) * sin(pos.y * 30.0 + t * 0.3) * 0.002;
    displacement += vec2(wrinkle, wrinkle * 0.5);

    return displacement;
}
