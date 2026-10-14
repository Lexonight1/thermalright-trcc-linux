// KarmaViz warp map "Vortex Distortion" (distortion): Swirling vortex effect with distance-based rotation strength
// From KarmaViz 67e8a15 warp_maps/distortion/vortex_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Vortex Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Vortex strength decreases with distance
    float vortex_strength = 3.0 / (1.0 + radius * 4.0);
    float rotation_angle = vortex_strength * (sin(t * 0.8) * 0.5 + 0.5);

    // Apply vortex rotation
    float cos_a = cos(rotation_angle);
    float sin_a = sin(rotation_angle);

    vec2 vortex = vec2(
        centered.x * cos_a - centered.y * sin_a,
        centered.x * sin_a + centered.y * cos_a
    );

    return (vortex - centered) * 0.5;
}
