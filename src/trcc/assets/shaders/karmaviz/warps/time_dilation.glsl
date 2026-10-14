// KarmaViz warp map "time_dilation" (experimental): Relativistic time dilation effects
// From KarmaViz 67e8a15 warp_maps/experimental/time_dilation.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Time Dilation Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    float dist = length(offset);
    
    // Simulate massive object at center
    float mass_effect = 1.0 / (1.0 + dist * 5.0);
    
    // Time dilation factor
    float time_factor = sqrt(1.0 - mass_effect * 0.9);
    
    // Warped time
    float warped_time = t * time_factor;
    
    // Spacetime curvature
    float curvature = sin(dist * 10.0 - warped_time * 3.0) * mass_effect;
    
    // Gravitational lensing
    vec2 lensing = normalize(offset) * curvature * 0.02;
    
    return lensing;
}
