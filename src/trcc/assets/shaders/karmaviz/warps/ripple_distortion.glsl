// KarmaViz warp map "Ripple Distortion" (distortion): Multiple concentric ripple waves emanating from the center
// From KarmaViz 67e8a15 warp_maps/distortion/ripple_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ripple Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Multiple ripple waves
    float wave1 = sin(radius * 15.0 - t * 3.0) * 0.02;
    float wave2 = sin(radius * 25.0 + t * 2.0) * 0.01;
    float wave3 = cos(radius * 35.0 - t * 4.0) * 0.005;

    float total_wave = wave1 + wave2 + wave3;

    // Apply ripple displacement radially
    vec2 direction = normalize(centered);
    if (radius < 0.001) direction = vec2(0.0);

    return direction * total_wave * (1.0 + radius * 2.0);
}
