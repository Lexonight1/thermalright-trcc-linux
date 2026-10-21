// KarmaViz warp map "lens_distortion" (basic): Optical lens distortion effect
// From KarmaViz 67e8a15 warp_maps/basic/lens_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Lens Distortion Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    float dist = length(offset);
    
    // Barrel distortion
    float distortion = 1.0 + 0.3 * dist * dist;
    
    // Animate the distortion strength
    float animated_strength = 0.8 + 0.4 * sin(t * 0.5);
    
    return offset * (distortion - 1.0) * animated_strength * 0.1;
}
