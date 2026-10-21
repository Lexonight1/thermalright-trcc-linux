// KarmaViz warp map "Twist Distortion" (distortion): Creates a twisting spiral effect that rotates based on distance from center
// From KarmaViz 67e8a15 warp_maps/distortion/twist_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Twist Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Twist angle based on radius and time
    float twist_strength = 2.0 + sin(t * 0.4) * 1.0;
    float angle = radius * twist_strength + t * 0.5;

    // Apply rotation matrix
    float cos_a = cos(angle);
    float sin_a = sin(angle);

    vec2 twisted = vec2(
        centered.x * cos_a - centered.y * sin_a,
        centered.x * sin_a + centered.y * cos_a
    );

    return (twisted - centered) * 0.4;
}
