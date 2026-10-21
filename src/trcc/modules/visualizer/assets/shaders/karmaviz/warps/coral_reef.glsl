// KarmaViz warp map "Coral Reef" (organic): Multiple coral formations with organic branching patterns and natural growth
// From KarmaViz 67e8a15 warp_maps/organic/coral_reef.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Coral Reef
vec2 get_pattern(vec2 pos, float t) {
    vec2 coral_pos = pos * 6.0;

    // Multiple coral formations
    vec2 displacement = vec2(0.0);

    for (float i = 0.0; i < 4.0; i += 1.0) {
        vec2 coral_center = vec2(
            sin(i * 2.5 + t * 0.3) * 2.0 + 3.0,
            cos(i * 3.2 + t * 0.4) * 2.0 + 3.0
        );

        vec2 to_coral = coral_pos - coral_center;
        float dist = length(to_coral);

        // Coral growth pattern
        float coral_size = 1.5 + sin(i * 4.0 + t * 0.6) * 0.5;

        if (dist < coral_size) {
            // Inside coral - create organic branching
            float branch_pattern = sin(dist * 8.0 + t * 2.0) * sin(atan(to_coral.y, to_coral.x) * 6.0 + t);
            vec2 branch_dir = normalize(to_coral);

            displacement += branch_dir * branch_pattern * 0.02 * (1.0 - dist / coral_size);
        }
    }

    return displacement;
}
