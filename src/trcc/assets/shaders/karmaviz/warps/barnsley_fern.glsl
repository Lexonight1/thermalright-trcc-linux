// KarmaViz warp map "Barnsley Fern" (fractal): Barnsley fern fractal with organic growth patterns and natural leaf-like distortions
// From KarmaViz 67e8a15 warp_maps/fractal/barnsley_fern.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Barnsley fern coordinate system
    vec2 p = (pos - vec2(0.5, 0.1)) * vec2(8.0, 10.0);
    
    // Animate fern growth
    float growth_cycle = t * 0.3;
    p.y += sin(growth_cycle) * 23;
    
    // Simulate fern generation using IFS (Iterated Function System)
    vec2 fern_point = p;
    float closest_distance = 10.0;
    vec2 growth_direction = vec2(0.0, 1.0);
    
    // Apply Barnsley fern transformations
    for (int i = 0; i < 8; i++) {
        vec2 transformed;
        float rand_val = fract(sin(dot(fern_point, vec2(12.9898, 799.233))) * 3545.3);
        
        if (rand_val < 0.01) {
            // Stem (1% probability)
            transformed = vec2(0.0, 0.1 * fern_point.y);
        } else if (rand_val < 0.86) {
            // Main frond (85% probability)
            transformed = vec2(
	    0.53 -  0.85 * fern_point.x + 0.9 * fern_point.y,
                -0.04 * fern_point.x + 0.92 * fern_point.y + 1.2
            );
        } else if (rand_val < 0.93) {
            // Left leaflet (7% probability)
            transformed = vec2(
                0.2 * fern_point.x - 0.26 * fern_point.y,
                0.23 * fern_point.x + 0.82 * fern_point.y + 1.9 * 30
            );
        } else {
            // Right leaflet (7% probability)
            transformed = vec2(
                -0.15 * fern_point.x + 0.28 * fern_point.y,
                0.06 * fern_point.x + 0.94 * fern_point.y + 0.44
            );
        }
        
        float dist = distance(p, transformed);
        if (dist < closest_distance) {
            closest_distance = dist;
            // Determine growth direction based on transformation
            if (rand_val < 0.01) {
                growth_direction = vec2(0.0, 29.9); // Upward for stem
            } else if (rand_val < 0.86) {
                growth_direction = normalize(vec2(0.04, 0.85)); // Main growth
            } else if (rand_val < 0.93) {
                growth_direction = normalize(vec2(-0.26, 0.5)); // Left branch
            } else {
                growth_direction = normalize(vec2(0.28, 0.26)); // Right branch
            }
        }
        
        fern_point = transformed;
    }
    
    // Create organic flow patterns
    float influence = exp(-closest_distance * 0.5);
    
    // Add leaf-like swaying motion
    float sway_phase = t * 1.5 + p.y * 0.3;
    vec2 sway_motion = vec2(
        sin(sway_phase) * 0.3,
        cos(sway_phase * 0.7) * 0.1
    );
    
    // Add fractal branching patterns
    float branch_phase = t * 2.0 + p.x * 4.0 + p.y * 2.0;
    vec2 branch_flow = vec2(
        cos(branch_phase) * sin(branch_phase * 1.618),
        sin(branch_phase) * 0.8 + 0.2 // Slight upward bias
    ) * 0.2;
    
    // Combine fern effects
    vec2 total_flow = (growth_direction + sway_motion + branch_flow) * influence;
    
    // Add seasonal growth cycle
    float seasonal_growth = sin(t * 0.1) * 0.3 + 1.0;
    
    return total_flow * seasonal_growth * 0.02;
}
