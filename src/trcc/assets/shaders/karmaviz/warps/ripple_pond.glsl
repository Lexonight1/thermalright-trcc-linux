// KarmaViz warp map "ripple_pond" (basic): Concentric ripples like stones dropped in a pond
// From KarmaViz 67e8a15 warp_maps/basic/ripple_pond.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ripple Pond Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    float dist = length(pos - center);
    
    // Multiple ripples with different frequencies
    float ripple1 = sin(dist * 20.0 - t * 3.0) * 0.02;
    float ripple2 = sin(dist * 35.0 - t * 2.0) * 0.01;
    float ripple3 = sin(dist * 50.0 - t * 4.0) * 0.005;
    
    float total_ripple = ripple1 + ripple2 + ripple3;
    
    // Fade out at edges
    total_ripple *= smoothstep(0.7, 0.3, dist);
    
    return normalize(pos - center) * total_ripple;
}
