// KarmaViz warp map "Kaleidoscope Distortion" (basic): Kaleidoscope effect with variable segments and radial distortion
// From KarmaViz 67e8a15 warp_maps/basic/kaleidoscope_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Kaleidoscope Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.4;
    float angle = atan(centered.y, centered.x);
    float radius = length(centered);

    // Create kaleidoscope segments
    float segments = 6.0 + sin(t * 0.3) * 5.0;  // 4-8 segments
    float segment_angle = 2.0 * 3.14159 / segments;

    // Fold the angle into one segment
    angle = mod(angle + 3.14159, segment_angle) - segment_angle * 0.5;

    // Add some radial distortion
    float radial_distort = sin(radius * 8.0 + t * 2.0) * 0.1;
    radius += radial_distort;

    vec2 kaleidoscope = vec2(
        radius * cos(angle),
        radius * sin(angle)
    );

    return (kaleidoscope - centered) * 0.3;
}
