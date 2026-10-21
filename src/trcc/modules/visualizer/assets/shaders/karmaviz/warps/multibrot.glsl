// KarmaViz warp map "Multibrot" (fractal): Multibrot fractal with variable power iterations creating diverse geometric structures
// From KarmaViz 67e8a15 warp_maps/fractal/multibrot.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Multibrot fractal with animated power
    vec2 c = (pos - 0.5) * 3.0;
    
    // Animate the power parameter
    float power = 5.0 + sin(t * 0.2) * 1.5; // Power varies from 0.5 to 3.5
    
    // Animate viewing window
    float zoom = 1.0 + sin(t * 0.15) * 0.3;
    c = c / zoom;
    
    vec2 z = vec2(0.0);
    float iterations = 0.0;
    float max_iterations = 10.0;
    
    // Track orbit properties for flow generation
    vec2 orbit_center = vec2(0.0);
    float orbit_energy = 0.0;
    
    // Multibrot iteration: z = z^power + c
    for (float i = 0.0; i < max_iterations; i++) {
        if (dot(z, z) > 4.0) break;
        
        // Calculate z^power using polar form
        float r = length(z);
        float theta = atan(z.y, z.x);
        
        // z^power = r^power * (cos(power*theta) + i*sin(power*theta))
        float new_r = pow(r, power);
        float new_theta = power * theta;
        
        vec2 z_power = vec2(
            new_r * cos(new_theta),
            new_r * sin(new_theta)
        );
        
        z = z_power + c;
        
        // Track orbit properties
        orbit_center += z;
        orbit_energy += length(z);
        
        iterations += 1.0;
    }
    
    orbit_center /= iterations;
    orbit_energy /= iterations;
    
    // Calculate escape properties
    float escape_value = iterations / max_iterations;
    
    // Create power-dependent flow patterns
    vec2 power_flow;
    
    if (escape_value < 0.3) {
        // Inside - create power-based spiral
        float spiral_angle = atan(pos.y - 0.5, pos.x - 0.5) * power + t * 1.5;
        float spiral_radius = length(pos - 0.5);
        
        power_flow = vec2(
            cos(spiral_angle) * spiral_radius,
            sin(spiral_angle) * spiral_radius
        ) * 0.5;
    } else {
        // Outside - create power-modulated escape
        float escape_angle = escape_value * 6.28 * power + t;
        power_flow = vec2(
            cos(escape_angle),
            sin(escape_angle)
        ) * (1.0 - escape_value);
    }
    
    // Add multibrot-specific symmetry effects
    float symmetry_factor = power;
    float symmetry_angle = atan(pos.y - 0.5, pos.x - 0.5) * symmetry_factor + t * 0.8;
    vec2 symmetry_flow = vec2(
        cos(symmetry_angle),
        sin(symmetry_angle)
    ) * 0.3;
    
    // Add orbit-based turbulence
    float orbit_phase = t * 2.0 + orbit_energy;
    vec2 orbit_turbulence = vec2(
        sin(orbit_phase) * cos(orbit_phase * power),
        cos(orbit_phase) * sin(orbit_phase / power)
    ) * 0.2;
    
    // Combine effects
    vec2 total_flow = power_flow + symmetry_flow + orbit_turbulence;
    
    // Modulate by power parameter
    float power_modulation = (power - 1.0) * 0.5 + 1.0;
    
    return total_flow * power_modulation * 0.025;
}
