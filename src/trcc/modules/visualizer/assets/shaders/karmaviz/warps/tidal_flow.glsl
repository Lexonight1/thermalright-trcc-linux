// KarmaViz warp map "Tidal Flow" (motion): Gravitational tidal forces with ebb and flow cycles
// From KarmaViz 67e8a15 warp_maps/motion/tidal_flow.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Simulate tidal forces with multiple gravitational sources
    vec2 moon1 = vec2(0.8 + sin(t * 0.5) * 0.3, 0.5);
    vec2 moon2 = vec2(0.2 + cos(t * 0.3) * 0.2, 0.3 + sin(t * 0.4) * 0.4);
    
    // Calculate gravitational influences
    float dist1 = distance(pos, moon1);
    float dist2 = distance(pos, moon2);
    
    // Tidal force (inverse square law, but softened)
    vec2 force1 = normalize(moon1 - pos) / (dist1 * dist1 + 0.1);
    vec2 force2 = normalize(moon2 - pos) / (dist2 * dist2 + 0.1);
    
    vec2 tidal_force = force1 * 0.3 + force2 * 0.2;
    
    // Add tidal wave motion
    float wave_phase = pos.x * 4.0 + t * 2.0;
    float tidal_wave = sin(wave_phase) * cos(pos.y * 3.0 + t * 1.5);
    
    vec2 wave_motion = vec2(
        cos(wave_phase) * 0.3,
        tidal_wave * 0.4
    );
    
    // Combine forces
    vec2 total_motion = tidal_force + wave_motion;
    
    // Add ebb and flow cycle
    float ebb_flow = sin(t * 0.8) * 0.5 + 0.5;
    
    return total_motion * ebb_flow * 0.02;
}
