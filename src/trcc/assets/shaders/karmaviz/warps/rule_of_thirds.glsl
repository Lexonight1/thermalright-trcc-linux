// KarmaViz warp map "Rule of Thirds" (basic): Rule of thirds grid with power point attraction and magnetic grid lines
// From KarmaViz 67e8a15 warp_maps/basic/rule_of_thirds.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Rule of Thirds Grid Transformation
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    
    // Rule of thirds division points
    float third = 1.0 / 3.0;
    float two_thirds = 2.0 / 3.0;
    
    // Grid lines at 1/3 and 2/3
    vec2 grid_pos = pos;
    
    // Horizontal thirds
    float h1_dist = abs(grid_pos.y - third);
    float h2_dist = abs(grid_pos.y - two_thirds);
    
    // Vertical thirds
    float v1_dist = abs(grid_pos.x - third);
    float v2_dist = abs(grid_pos.x - two_thirds);
    
    // Find closest grid line
    float min_h_dist = min(h1_dist, h2_dist);
    float min_v_dist = min(v1_dist, v2_dist);
    float min_dist = min(min_h_dist, min_v_dist);
    
    // Grid line influence
    float grid_influence = exp(-min_dist * 20.0);
    
    // Power points (intersections of thirds)
    vec2 power_points[4] = vec2[](
        vec2(third, third),
        vec2(two_thirds, third),
        vec2(third, two_thirds),
        vec2(two_thirds, two_thirds)
    );
    
    vec2 displacement = vec2(0.0);
    
    // Attraction to power points
    for (int i = 0; i < 4; i++) {
        vec2 to_power_point = power_points[i] - pos;
        float power_distance = length(to_power_point);
        
        // Power point influence with audio reactivity
        float power_influence = exp(-power_distance * 3.0) * (1.0 + sin(t * 2.0 + float(i)) * 0.5);
        
        displacement += normalize(to_power_point) * power_influence * 0.05;
    }
    
    // Grid line magnetic effect
    if (min_dist < 0.05) {
        vec2 grid_direction = vec2(0.0);
        
        // Determine which grid line we're near
        if (min_h_dist < min_v_dist) {
            // Near horizontal line
            grid_direction = vec2(sin(pos.x * 6.28318 + t * 2.0), 0.0);
        } else {
            // Near vertical line
            grid_direction = vec2(0.0, sin(pos.y * 6.28318 + t * 2.0));
        }
        
        displacement += grid_direction * grid_influence * 0.03;
    }
    
    // Add thirds-based wave pattern
    float thirds_wave = sin(pos.x * 3.0 * 3.14159 + t) * sin(pos.y * 3.0 * 3.14159 + t * 1.2);
    displacement += normalize(centered) * thirds_wave * 0.02;
    
    return displacement;
}
