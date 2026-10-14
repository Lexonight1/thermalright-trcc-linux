// KarmaViz warp map "circuit_trace" (digital): Electronic circuit board trace patterns
// From KarmaViz 67e8a15 warp_maps/digital/circuit_trace.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Circuit Trace Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Horizontal traces
    float h_trace = step(0.48, fract(pos.y * 20.0)) * step(fract(pos.y * 20.0), 0.52);
    if (h_trace > 0.5) {
        warp.x = sin(pos.x * 50.0 + t * 2.0) * 0.01;
    }
    
    // Vertical traces
    float v_trace = step(0.48, fract(pos.x * 15.0)) * step(fract(pos.x * 15.0), 0.52);
    if (v_trace > 0.5) {
        warp.y = sin(pos.y * 40.0 + t * 1.5) * 0.01;
    }
    
    // Connection points
    vec2 grid_pos = floor(pos * 10.0);
    float connection = step(0.9, fract(sin(dot(grid_pos, vec2(12.9898, 78.233))) * 43758.5453));
    
    if (connection > 0.5) {
        float pulse = sin(t * 8.0) * 0.02;
        warp += normalize(pos - vec2(0.5)) * pulse;
    }
    
    return warp;
}
