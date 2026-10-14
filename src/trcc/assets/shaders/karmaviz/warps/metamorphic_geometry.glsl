// KarmaViz warp map "Metamorphic Geometry" (experimental): Shape-shifting geometry that morphs between circle, square, triangle, and hexagon
// From KarmaViz 67e8a15 warp_maps/experimental/metamorphic_geometry.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Metamorphic Geometry
vec2 get_pattern(vec2 pos, float t) {
    // Shape transformation cycle
    float morph_cycle = sin(t * 0.3) * 0.5 + 0.5; // 0 to 1

    // Base shapes to morph between
    vec2 centered = pos - 0.5;

    // Circle to square transformation
    float circle_dist = length(centered);
    float square_dist = max(abs(centered.x), abs(centered.y));
    float shape_dist = mix(circle_dist, square_dist, morph_cycle);

    // Triangle transformation
    float triangle_dist = max(
        abs(centered.x) * 0.866 + centered.y * 0.5,
        -centered.y
    );
    triangle_dist = max(triangle_dist, abs(centered.x) * 0.866 - centered.y * 0.5);

    // Hexagon transformation
    float hex_angle = atan(centered.y, centered.x);
    float hex_radius = length(centered);
    float hex_side = cos(mod(hex_angle + 3.14159/6.0, 3.14159/3.0) - 3.14159/6.0);
    float hex_dist = hex_radius * hex_side;

    // Complex morphing sequence
    float morph_phase = fract(t * 0.2) * 4.0; // 4 phases
    float current_dist;

    if (morph_phase < 1.0) {
        current_dist = mix(circle_dist, square_dist, morph_phase);
    } else if (morph_phase < 2.0) {
        current_dist = mix(square_dist, triangle_dist, morph_phase - 1.0);
    } else if (morph_phase < 3.0) {
        current_dist = mix(triangle_dist, hex_dist, morph_phase - 2.0);
    } else {
        current_dist = mix(hex_dist, circle_dist, morph_phase - 3.0);
    }

    // Metamorphic field distortion
    vec2 gradient = normalize(centered);
    float field_strength = exp(-current_dist * 4.0) * sin(current_dist * 15.0 + t * 2.0);

    return gradient * field_strength * 0.08;
}
