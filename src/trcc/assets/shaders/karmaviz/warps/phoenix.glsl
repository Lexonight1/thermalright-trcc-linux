// KarmaViz warp map "Phoenix" (fractal): Phoenix fractal with rebirth cycles and flame-like regenerative distortions
// From KarmaViz 67e8a15 warp_maps/fractal/phoenix.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Phoenix fractal parameters
    vec2 c = (pos - 0.5) * 3.0;
    
    // Animate phoenix parameters
    float phoenix_cycle = t * 0.2;
    float p = 0.5667 + sin(phoenix_cycle) * 0.1; // Phoenix parameter
    
    vec2 z = vec2(0.0);
    vec2 z_prev = vec2(0.0);
    float iterations = 0.0;
    float max_iterations = 10.0;
    
    // Track phoenix rebirth cycles
    float rebirth_energy = 0.0;
    
    // Phoenix iteration: z = z^2 + c + p * z_prev
    for (float i = 0.0; i < max_iterations; i++) {
        if (dot(z, z) > 4.0) break;
        
        vec2 z_new = vec2(
            z.x * z.x - z.y * z.y + c.x + p * z_prev.x,
            2.0 * z.x * z.y + c.y + p * z_prev.y
        );
        
        // Track energy for rebirth effect
        float energy_change = length(z_new) - length(z);
        rebirth_energy += abs(energy_change);
        
        z_prev = z;
        z = z_new;
        iterations += 1.0;
    }
    
    // Calculate phoenix properties
    float escape_value = iterations / max_iterations;
    float phoenix_energy = rebirth_energy / max_iterations;
    
    // Create flame-like flow patterns
    vec2 flame_flow;
    if (escape_value < 0.4) {
        // Inside - create rising flame motion
        float flame_angle = t * 2.5 + pos.x * 8.0;
        float flame_height = sin(flame_angle) * 0.3 + 0.7; // Bias upward
        
        flame_flow = vec2(
            sin(flame_angle * 1.3) * 0.4,
            flame_height
        ) * phoenix_energy;
    } else {
        // Outside - create ash and ember patterns
        float ember_angle = escape_value * 6.28 + t * 1.8;
        float ember_drift = sin(t * 0.8 + pos.y * 4.0) * 0.3;
        
        flame_flow = vec2(
            cos(ember_angle) + ember_drift,
            sin(ember_angle) * 0.5 - 0.2 // Slight downward drift
        ) * (1.0 - escape_value);
    }
    
    // Add rebirth cycle effects
    float rebirth_phase = phoenix_cycle * 2.0 + phoenix_energy * 3.0;
    vec2 rebirth_flow = vec2(
        cos(rebirth_phase) * sin(rebirth_phase * 1.414),
        sin(rebirth_phase) * cos(rebirth_phase * 0.707)
    ) * 0.4;
    
    // Add phoenix wing patterns
    float wing_span = abs(pos.x - 0.5) * 4.0;
    float wing_beat = sin(t * 3.0 + wing_span) * 0.3;
    vec2 wing_flow = vec2(0.0, wing_beat) * exp(-wing_span);
    
    // Combine all phoenix effects
    vec2 total_flow = flame_flow + rebirth_flow + wing_flow;
    
    // Add phoenix breathing cycle
    float breathing = sin(t * 1.5) * 0.2 + 1.0;
    
    return total_flow * breathing * 0.025;
}
