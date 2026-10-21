// KarmaViz warp map "Möbius Strip Topology" (experimental): Non-orientable surface topology with half-twist transformation and dimensional projection
// From KarmaViz 67e8a15 warp_maps/experimental/möbius_strip_topology.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Möbius Strip Topology
vec2 get_pattern(vec2 pos, float t) {
    // Convert to cylindrical coordinates
    vec2 centered = pos - 0.5;
    float radius = length(centered) + 0.1;
    float angle = atan(centered.y, centered.x);

    // Möbius strip parameter
    float strip_width = 0.3;
    float u = (radius - 0.2) / strip_width; // Position across strip width
    float v = angle / (2.0 * 3.14159);      // Position along strip length

    // Möbius transformation: half twist over full length
    float twist_angle = v * 3.14159 + t * 0.5; // Half twist + rotation

    // Apply Möbius twist
    float twisted_u = u * cos(twist_angle);
    float height = u * sin(twist_angle);

    // Project back to 2D with topology distortion
    vec2 mobius_pos = vec2(
        (0.2 + twisted_u * strip_width) * cos(v * 2.0 * 3.14159),
        (0.2 + twisted_u * strip_width) * sin(v * 2.0 * 3.14159)
    );

    // Add height as additional distortion
    mobius_pos += vec2(height * sin(v * 4.0 * 3.14159), height * cos(v * 4.0 * 3.14159)) * 0.1;

    // Non-orientable surface effects
    float orientation = sign(sin(v * 2.0 * 3.14159));
    mobius_pos *= 1.0 + orientation * height * 0.2;

    return (mobius_pos - centered) * 0.4;
}
