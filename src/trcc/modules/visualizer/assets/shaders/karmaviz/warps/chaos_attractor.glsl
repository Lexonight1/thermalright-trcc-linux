// KarmaViz warp map "chaos_attractor" (experimental): Strange attractor field distortion
// From KarmaViz 67e8a15 warp_maps/experimental/chaos_attractor.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Chaos Attractor Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Lorenz attractor parameters
    float sigma = 10.0;
    float rho = 28.0;
    float beta = 8.0/3.0;
    
    // Map screen space to attractor space
    vec3 state = vec3(
        (pos.x - 0.5) * 20.0,
        (pos.y - 0.5) * 20.0,
        sin(t * 0.1) * 10.0
    );
    
    // Simulate attractor dynamics
    for (int i = 0; i < 5; i++) {
        vec3 derivative = vec3(
            sigma * (state.y - state.x),
            state.x * (rho - state.z) - state.y,
            state.x * state.y - beta * state.z
        );
        
        state += derivative * 0.01;
    }
    
    // Project back to 2D warp
    warp = vec2(state.x, state.y) * 0.001;
    
    return warp;
}
