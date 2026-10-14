// KarmaViz warp map "quantum_tunnel" (futuristic): Quantum tunneling effect visualization
// From KarmaViz 67e8a15 warp_maps/futuristic/quantum_tunnel.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Quantum Tunnel Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 to_center = center - pos;
    float dist = length(to_center);
    
    // Quantum probability wave
    float wave = sin(dist * 20.0 - t * 5.0) * exp(-dist * 2.0);
    
    // Tunneling effect
    float tunnel_prob = exp(-dist * 3.0);
    
    // Uncertainty principle
    float uncertainty_x = sin(pos.x * 50.0 + t * 8.0) * 0.002;
    float uncertainty_y = cos(pos.y * 60.0 + t * 6.0) * 0.002;
    
    vec2 warp = normalize(to_center) * wave * tunnel_prob * 0.05;
    warp += vec2(uncertainty_x, uncertainty_y);
    
    return warp;
}
