// KarmaViz warp map "flowing_water" (organic): Natural water flow patterns
// From KarmaViz 67e8a15 warp_maps/organic/flowing_water.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Flowing Water Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 flow = vec2(0.0);
    
    // Main current
    flow.x = sin(pos.y * 8.0 + t * 2.0) * 0.02;
    flow.y = cos(pos.x * 6.0 + t * 1.5) * 0.015;
    
    // Turbulence
    flow.x += sin(pos.y * 20.0 + pos.x * 15.0 + t * 3.0) * 0.008;
    flow.y += cos(pos.x * 18.0 + pos.y * 12.0 + t * 2.5) * 0.006;
    
    // Eddies
    vec2 eddy_center = vec2(0.3 + sin(t * 0.3) * 0.2, 0.7 + cos(t * 0.4) * 0.2);
    vec2 to_eddy = pos - eddy_center;
    float eddy_dist = length(to_eddy);
    
    if (eddy_dist < 0.2) {
        float eddy_strength = (0.2 - eddy_dist) / 0.2;
        vec2 eddy_flow = vec2(-to_eddy.y, to_eddy.x) * eddy_strength * 0.03;
        flow += eddy_flow;
    }
    
    return flow;
}
