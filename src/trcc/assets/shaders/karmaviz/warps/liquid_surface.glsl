// KarmaViz warp map "Liquid Surface" (motion): Fluid dynamics with surface waves, currents, and bubble motion
// From KarmaViz 67e8a15 warp_maps/motion/liquid_surface.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Simulate liquid surface with multiple disturbances
    float surface_level = 0.6;
    
    // Create surface waves
    float wave1 = sin(pos.x * 8.0 - t * 2.0) * 0.05;
    float wave2 = sin(pos.x * 12.0 - t * 1.5 + 1.0) * 0.03;
    float wave3 = sin(pos.x * 6.0 - t * 2.5 + 2.0) * 0.04;
    
    float surface_height = surface_level + wave1 + wave2 + wave3;
    
    vec2 liquid_motion = vec2(0.0);
    
    if (pos.y < surface_height) {
        // Below surface - liquid flow
        float depth = surface_height - pos.y;
        float flow_strength = depth * 2.0;
        
        // Horizontal currents
        float current_phase = t * 1.0 + pos.x * 3.0;
        vec2 current = vec2(sin(current_phase) * 0.5, 0.0);
        
        // Vertical convection
        float convection_phase = pos.x * 5.0 + t * 1.5;
        vec2 convection = vec2(0.0, sin(convection_phase) * cos(pos.y * 8.0) * 0.3);
        
        // Viscous flow
        float viscosity_factor = 1.0 - exp(-depth * 3.0);
        liquid_motion = (current + convection) * flow_strength * viscosity_factor;
        
        // Add bubble motion
        float bubble_phase = t * 3.0 + pos.x * 7.0 + pos.y * 5.0;
        if (sin(bubble_phase) > 0.7) {
            liquid_motion += vec2(sin(bubble_phase * 2.0) * 0.1, 0.2);
        }
    } else {
        // Above surface - air currents
        float height_above = pos.y - surface_height;
        float air_influence = exp(-height_above * 5.0);
        
        // Surface tension effects
        vec2 surface_gradient = vec2(
            (wave1 + wave2 + wave3) * 8.0 * cos(pos.x * 8.0 - t * 2.0),
            0.0
        );
        
        liquid_motion = surface_gradient * air_influence * 0.3;
    }
    
    // Add droplet effects
    float droplet_phase = t * 4.0 + pos.x * 10.0;
    if (pos.y > surface_height && sin(droplet_phase) > 0.8) {
        liquid_motion += vec2(0.0, -0.5); // Gravity
    }
    
    return liquid_motion * 0.02;
}
