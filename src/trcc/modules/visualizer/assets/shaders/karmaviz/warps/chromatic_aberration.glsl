// KarmaViz warp map "Chromatic Aberration" (distortion): Simulates chromatic aberration lens distortion with color channel separation
// From KarmaViz 67e8a15 warp_maps/distortion/chromatic_aberration.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Chromatic Aberration Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Simulate chromatic aberration with radial distortion
    float aberration_strength = 0.02 + sin(t * 0.6) * 0.01;

    // Different distortion for different "color channels"
    float red_distort = 1.0 + aberration_strength * radius * radius;
    float green_distort = 1.0;  // Green stays centered
    float blue_distort = 1.0 - aberration_strength * radius * radius;

    // Use the average distortion for the displacement
    float avg_distort = (red_distort + blue_distort) * 0.5;

    vec2 direction = normalize(centered);
    if (radius < 0.001) direction = vec2(0.0);

    vec2 aberrated = direction * radius * avg_distort;
    return (aberrated - centered) * 0.4;
}
