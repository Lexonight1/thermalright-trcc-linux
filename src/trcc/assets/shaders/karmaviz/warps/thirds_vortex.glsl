// KarmaViz warp map "Thirds Vortex" (experimental): Vortex system positioned at rule of thirds power points with spiral dynamics
// From KarmaViz 67e8a15 warp_maps/experimental/thirds_vortex.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Rule of Thirds Vortex System
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Rule of thirds intersection points (power points)
    vec2 vortex_centers[4] = vec2[](
        vec2(-1.0/6.0, -1.0/6.0),  // Bottom-left third intersection
        vec2(1.0/6.0, -1.0/6.0),   // Bottom-right third intersection
        vec2(-1.0/6.0, 1.0/6.0),   // Top-left third intersection
        vec2(1.0/6.0, 1.0/6.0)     // Top-right third intersection
    );

    vec2 displacement = vec2(0.0);

    // Create vortices at each power point
    for (int i = 0; i < 4; i++) {
        vec2 to_vortex = centered - vortex_centers[i];
        float vortex_distance = length(to_vortex);

        // Vortex strength based on thirds positioning
        float vortex_strength = 1.0 / (vortex_distance + 0.1);

        // Rotational component
        float rotation_angle = atan(to_vortex.y, to_vortex.x);
        float vortex_rotation = rotation_angle + vortex_strength * 0.5 + t * (1.0 + float(i) * 0.5);

        // Spiral inward/outward based on time and vortex
        float spiral_factor = sin(t * 2.0 + float(i) * 1.5708) * 0.5 + 0.5; // 0 to 1
        float target_radius = vortex_distance * (0.8 + spiral_factor * 0.4);

        vec2 vortex_target = vortex_centers[i] + vec2(
            target_radius * cos(vortex_rotation),
            target_radius * sin(vortex_rotation)
        );

        vec2 vortex_displacement = vortex_target - centered;
        float vortex_influence = exp(-vortex_distance * 2.0);

        displacement += vortex_displacement * vortex_influence * 0.1;
    }

    // Add thirds-based field lines
    float thirds_field_x = sin(pos.y * 3.0 * 3.14159 + t) * 0.02;
    float thirds_field_y = sin(pos.x * 3.0 * 3.14159 + t * 1.2) * 0.02;

    displacement += vec2(thirds_field_x, thirds_field_y);

    // Central stabilizing force
    float center_distance = length(centered);
    vec2 center_force = -normalize(centered) * center_distance * 0.01;
    displacement += center_force;

    return displacement;
}
