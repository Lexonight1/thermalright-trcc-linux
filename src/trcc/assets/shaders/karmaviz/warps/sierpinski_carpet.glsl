// KarmaViz warp map "Sierpinski Carpet" (fractal): Sierpinski carpet fractal with recursive square subdivisions and geometric flow patterns
// From KarmaViz 67e8a15 warp_maps/fractal/sierpinski_carpet.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Sierpinski carpet calculation
    vec2 p = pos * 3.0; // Scale up for detail
    
    // Animate the carpet
    float rotation = t * 0.1;
    float cos_r = cos(rotation);
    float sin_r = sin(rotation);
    p = vec2(
        p.x * cos_r - p.y * sin_r,
        p.x * sin_r + p.y * cos_r
    );
    
    float carpet_value = 1.0;
    vec2 flow_accumulator = vec2(0.0);
    
    // Iterate through carpet levels
    for (int level = 0; level < 5; level++) {
        vec2 cell = floor(p);
        vec2 local_pos = fract(p);
        
        // Check if we're in the middle third (removed square)
        if (abs(mod(cell.x, 3.0) - 1.0) < 0.1 && abs(mod(cell.y, 3.0) - 1.0) < 0.1) {
            carpet_value = 0.0;
            
            // Create outward flow from removed squares
            vec2 center_offset = local_pos - 0.5;
            float distance_to_center = length(center_offset);
            
            if (distance_to_center > 0.01) {
                vec2 outward_flow = normalize(center_offset) * (1.0 - distance_to_center);
                flow_accumulator += outward_flow * 0.3;
            }
        } else {
            // Create inward flow toward solid areas
            vec2 center_offset = local_pos - 0.5;
            float distance_to_center = length(center_offset);
            
            if (distance_to_center > 0.01) {
                vec2 inward_flow = -normalize(center_offset) * distance_to_center * 0.1;
                flow_accumulator += inward_flow;
            }
        }
        
        // Scale for next iteration
        p *= 3.0;
    }
    
    // Add recursive geometric patterns
    float geometric_phase = t * 1.5 + pos.x * 8.0 + pos.y * 6.0;
    vec2 geometric_flow = vec2(
        sin(geometric_phase) * cos(geometric_phase * 1.3),
        cos(geometric_phase) * sin(geometric_phase * 0.7)
    ) * carpet_value * 0.2;
    
    // Combine flows
    vec2 total_flow = flow_accumulator + geometric_flow;
    
    // Add breathing effect
    float breathing = sin(t * 2.0) * 0.1 + 1.0;
    
    return total_flow * breathing * 0.02;
}
