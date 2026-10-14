// KarmaViz warp map "Dragon Curve" (fractal): Heighway dragon curve fractal with serpentine flow patterns and recursive turns
// From KarmaViz 67e8a15 warp_maps/fractal/dragon_curve.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Dragon curve approximation using L-system rules
    vec2 p = (pos - 0.5) * 8.0;
    
    // Animate the dragon
    float dragon_time = t * 0.5;
    p += vec2(sin(dragon_time * 0.3), cos(dragon_time * 0.4)) * 2.0;
    
    // Dragon curve generation (simplified)
    vec2 dragon_pos = vec2(0.0);
    vec2 dragon_dir = vec2(1.0, 0.0);
    float step_size = 0.5;
    float closest_distance = 1000.0;
    vec2 closest_direction = vec2(0.0);
    
    // Simulate dragon curve path
    for (int i = 0; i < 16; i++) {
        // Dragon curve turn sequence (approximated)
        float turn_angle = 1.5708; // 90 degrees
        if (mod(float(i), 4.0) == 1.0 || mod(float(i), 4.0) == 2.0) {
            turn_angle = -1.5708; // -90 degrees
        }
        
        // Apply turn
        float cos_turn = cos(turn_angle);
        float sin_turn = sin(turn_angle);
        dragon_dir = vec2(
            dragon_dir.x * cos_turn - dragon_dir.y * sin_turn,
            dragon_dir.x * sin_turn + dragon_dir.y * cos_turn
        );
        
        // Move along dragon path
        dragon_pos += dragon_dir * step_size;
        
        // Check distance to current point
        float dist = distance(p, dragon_pos);
        if (dist < closest_distance) {
            closest_distance = dist;
            closest_direction = dragon_dir;
        }
        
        // Reduce step size for finer detail
        step_size *= 0.9;
    }
    
    // Create flow along dragon curve
    vec2 dragon_flow = closest_direction;
    
    // Add serpentine motion
    float serpentine_phase = t * 2.0 + closest_distance * 3.0;
    vec2 serpentine_offset = vec2(
        -closest_direction.y * sin(serpentine_phase),
        closest_direction.x * sin(serpentine_phase)
    ) * 0.5;
    
    // Distance-based influence
    float influence = exp(-closest_distance * 0.8);
    
    // Add recursive turning patterns
    float turn_frequency = 4.0;
    float turn_phase = t * 1.5 + pos.x * turn_frequency + pos.y * turn_frequency;
    vec2 recursive_turns = vec2(
        cos(turn_phase) * sin(turn_phase * 1.618),
        sin(turn_phase) * cos(turn_phase * 0.618)
    ) * 0.3;
    
    // Combine all dragon effects
    vec2 total_flow = (dragon_flow + serpentine_offset + recursive_turns) * influence;
    
    // Add breathing dragon effect
    float breathing = sin(t * 1.2) * 0.2 + 1.0;
    
    return total_flow * breathing * 0.025;
}
