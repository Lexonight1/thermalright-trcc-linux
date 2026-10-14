// KarmaViz warp map "Pincushion Distortion" (distortion): Pincushion distortion effect that pulls edges inward, opposite of barrel distortion
// From KarmaViz 67e8a15 warp_maps/distortion/pincushion_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Pincushion Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Pincushion distortion (negative barrel)
    float k1 = -0.4 - sin(t * 0.3) * 0.1;  // Animated negative distortion

    // Apply pincushion distortion
    float radius_distorted = radius * (1.0 + k1 * radius * radius);

    vec2 direction = normalize(centered);
    if (radius < 0.001) direction = vec2(0.0);

    vec2 distorted = direction * radius_distorted;
    return distorted * 0.25;
}
