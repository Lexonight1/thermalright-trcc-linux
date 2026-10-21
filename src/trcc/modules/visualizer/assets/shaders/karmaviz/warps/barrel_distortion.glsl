// KarmaViz warp map "Barrel Distortion" (distortion): Classic barrel distortion effect like a wide-angle lens, with animated distortion strength
// From KarmaViz 67e8a15 warp_maps/distortion/barrel_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Barrel Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Barrel distortion coefficient
    float k1 = 0.3 + sin(t * 0.5) * 0.2;  // Animated distortion strength

    // Apply barrel distortion formula
    float radius_distorted = radius * (1.0 + k1 * radius * radius);

    // Calculate new position
    vec2 direction = normalize(centered);
    if (radius < 0.001) direction = vec2(0.0);

    vec2 distorted = direction * radius_distorted;
    return distorted * 0.3;  // Scale down the effect
}
