// KarmaViz warp map "breathing_space" (basic): Gentle breathing-like expansion and contraction
// From KarmaViz 67e8a15 warp_maps/basic/breathing_space.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Breathing Space Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    
    // Breathing pattern
    float breath = sin(t * 0.8) * 0.05;
    
    // Radial expansion/contraction
    return offset * breath;
}
