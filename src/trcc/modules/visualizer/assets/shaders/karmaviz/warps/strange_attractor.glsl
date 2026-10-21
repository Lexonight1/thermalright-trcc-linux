// KarmaViz warp map "Strange Attractor" (fractal): Lorenz-style strange attractor with chaotic flow patterns and butterfly-like dynamics
// From KarmaViz 67e8a15 warp_maps/fractal/strange_attractor.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Strange attractor parameters (Lorenz-like system)
    float sigma = 10.0;
    float rho = 28.0 + sin(t * 0.1) * 5.0; // Animate parameter
    float beta = 8.0 / 3.0;
    
    // Map screen coordinates to attractor space
    vec3 attractor_pos = vec3(
        (pos.x - 0.5) * 40.0,
        (pos.y - 0.5) * 40.0,
        20.0
    );
    
    // Simulate attractor trajectory
    vec3 trajectory_point = vec3(1.0, 1.0, 1.0); // Starting point
    float dt = 0.01;
    float closest_distance = 1000.0;
    vec3 flow_direction = vec3(0.0);
    
    // Integrate attractor equations
    for (int i = 0; i < 20; i++) {
        // Lorenz equations:
        // dx/dt = sigma * (y - x)
        // dy/dt = x * (rho - z) - y
        // dz/dt = x * y - beta * z
        
        vec3 derivative = vec3(
            sigma * (trajectory_point.y - trajectory_point.x),
            trajectory_point.x * (rho - trajectory_point.z) - trajectory_point.y,
            trajectory_point.x * trajectory_point.y - beta * trajectory_point.z
        );
        
        trajectory_point += derivative * dt;
        
        // Check distance to current screen position
        float dist = distance(attractor_pos.xy, trajectory_point.xy);
        if (dist < closest_distance) {
            closest_distance = dist;
            flow_direction = derivative;
        }
    }
    
    // Create flow based on attractor dynamics
    vec2 attractor_flow = flow_direction.xy;
    
    // Normalize and scale flow
    if (length(attractor_flow) > 0.01) {
        attractor_flow = normalize(attractor_flow) * min(length(attractor_flow) * 0.1, 1.0);
    }
    
    // Add butterfly effect - small changes create large differences
    float butterfly_phase = t * 3.0 + pos.x * 15.0 + pos.y * 12.0;
    vec2 butterfly_perturbation = vec2(
        sin(butterfly_phase) * cos(butterfly_phase * 1.732),
        cos(butterfly_phase) * sin(butterfly_phase * 0.577)
    ) * 0.1;
    
    // Add chaotic turbulence
    float chaos_x = pos.x * 8.0 + t * 2.0;
    float chaos_y = pos.y * 6.0 + t * 1.7;
    vec2 chaos_flow = vec2(
        sin(chaos_x) * cos(chaos_y + t),
        cos(chaos_x + t) * sin(chaos_y)
    ) * 0.3;
    
    // Distance-based influence
    float influence = exp(-closest_distance * 0.05);
    
    // Add strange attractor wings (butterfly effect visualization)
    float wing_distance = abs(pos.x - 0.5);
    float wing_flutter = sin(t * 4.0 + wing_distance * 10.0) * 0.2;
    vec2 wing_flow = vec2(0.0, wing_flutter) * exp(-wing_distance * 3.0);
    
    // Combine all chaotic effects
    vec2 total_flow = (attractor_flow + butterfly_perturbation + chaos_flow + wing_flow) * influence;
    
    // Add parameter-dependent modulation
    float param_modulation = (rho - 23.0) / 10.0; // Normalize parameter variation
    
    return total_flow * (0.8 + param_modulation * 0.4) * 0.02;
}
