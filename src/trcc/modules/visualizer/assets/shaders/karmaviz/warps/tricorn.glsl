// KarmaViz warp map "Tricorn" (fractal): Tricorn fractal (Mandelbar) with complex conjugate iterations creating unique horn-like structures
// From KarmaViz 67e8a15 warp_maps/fractal/tricorn.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Tricorn fractal coordinates
    vec2 c = (pos - vec2(0.5, 0.6)) * 3.0;
    
    // Animate viewing window
    float zoom = 1.0 + sin(t * 0.15) * 0.3;
    c = c / zoom + vec2(-0.5, 0.0);
    
    vec2 z = vec2(0.0);
    float iterations = 0.0;
    float max_iterations = 10.0;
    
    // Tricorn iteration: z = conj(z)^2 + c
    // where conj(z) is the complex conjugate
    for (float i = 0.0; i < max_iterations; i++) {
        if (dot(z, z) > 4.0) break;
        
        // Take complex conjugate (negate imaginary part)
        vec2 z_conj = vec2(z.x, -z.y);
        
        // Square the conjugate
        z = vec2(
            z_conj.x * z_conj.x - z_conj.y * z_conj.y + c.x,
            2.0 * z_conj.x * z_conj.y + c.y
        );
        
        iterations += 1.0;
    }
    
    // Calculate escape properties
    float escape_value = iterations / max_iterations;
    
    // Create horn-like flow patterns
    vec2 horn_flow;
    if (escape_value < 0.3) {
        // Inside - create inward spiral toward horns
        vec2 to_center = vec2(0.0, 0.0) - (pos - 0.5);
        float spiral_angle = atan(to_center.y, to_center.x) + t * 1.5;
        horn_flow = vec2(
            cos(spiral_angle) * length(to_center),
            sin(spiral_angle) * length(to_center)
        ) * 0.5;
    } else {
        // Outside - create tricorn-specific escape patterns
        float horn_angle = escape_value * 3.14159 + t * 0.8;
        
        // Create three-fold symmetry (tri-corn)
        float symmetry_angle = horn_angle * 3.0;
        horn_flow = vec2(
            cos(symmetry_angle),
            sin(symmetry_angle)
        ) * (1.0 - escape_value);
        
        // Add conjugate-inspired reflection
        if (mod(floor(horn_angle), 2.0) == 1.0) {
            horn_flow.y = -horn_flow.y;
        }
    }
    
    // Add tricorn-specific turbulence
    float tricorn_phase = t * 1.8 + pos.x * 6.0 - pos.y * 4.0;
    vec2 tricorn_turbulence = vec2(
        sin(tricorn_phase) * cos(tricorn_phase * 1.5),
        cos(tricorn_phase) * sin(tricorn_phase * 0.8)
    ) * 0.4;
    
    // Modulate by distance to set boundary
    float boundary_factor = smoothstep(0.2, 0.8, escape_value);
    
    // Combine effects
    vec2 total_flow = horn_flow + tricorn_turbulence * boundary_factor;
    
    // Add pulsing effect
    float pulse = sin(t * 2.5) * 0.15 + 1.0;
    
    return total_flow * pulse * 0.02;
}
