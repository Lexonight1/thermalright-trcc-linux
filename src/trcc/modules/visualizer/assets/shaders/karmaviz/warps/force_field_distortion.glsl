// KarmaViz warp map "force_field_distortion" (futuristic): Energy force field warping space
// From KarmaViz 67e8a15 warp_maps/futuristic/force_field_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Force Field Distortion Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Multiple force field generators
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        vec2 generator_pos = vec2(
            0.5 + sin(t * 0.3 + fi * 1.57) * 0.3,
            0.5 + cos(t * 0.4 + fi * 1.57) * 0.3
        );
        
        vec2 field_vector = pos - generator_pos;
        float field_dist = length(field_vector);
        
        // Force field strength (inverse square law)
        float field_strength = 0.01 / (field_dist * field_dist + 0.01);
        
        // Field direction (repulsive)
        vec2 field_dir = normalize(field_vector);
        
        // Oscillating field strength
        float oscillation = sin(t * 3.0 + fi * 2.0) * 0.5 + 0.5;
        
        warp += field_dir * field_strength * oscillation;
    }
    
    return warp;
}
