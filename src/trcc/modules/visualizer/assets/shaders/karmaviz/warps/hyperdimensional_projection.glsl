// KarmaViz warp map "Hyperdimensional Projection" (experimental): 4D hypercube rotation and projection to 2D with perspective distortions
// From KarmaViz 67e8a15 warp_maps/experimental/hyperdimensional_projection.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Hyperdimensional Projection
vec2 get_pattern(vec2 pos, float t) {
    // Project from 4D hypercube to 2D
    vec4 hyper_pos = vec4(pos.x, pos.y, sin(t * 0.8), cos(t * 0.6));

    // 4D rotation matrices (simplified)
    float angle_xy = t * 0.3;
    float angle_zw = t * 0.5;

    // Rotate in XY plane
    vec2 xy_rot = vec2(
        hyper_pos.x * cos(angle_xy) - hyper_pos.y * sin(angle_xy),
        hyper_pos.x * sin(angle_xy) + hyper_pos.y * cos(angle_xy)
    );

    // Rotate in ZW plane
    vec2 zw_rot = vec2(
        hyper_pos.z * cos(angle_zw) - hyper_pos.w * sin(angle_zw),
        hyper_pos.z * sin(angle_zw) + hyper_pos.w * cos(angle_zw)
    );

    // Project 4D to 2D using perspective division
    float w_factor = 1.0 + zw_rot.y * 0.5; // W coordinate affects perspective
    vec2 projected = xy_rot / w_factor;

    // Add hyperdimensional distortions
    float hyper_field = sin(projected.x * 10.0 + zw_rot.x * 5.0 + t) *
                       cos(projected.y * 8.0 + zw_rot.y * 6.0 + t);

    vec2 displacement = (projected - pos) * 0.2 + vec2(hyper_field) * 0.03;

    return displacement;
}
