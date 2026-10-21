// KarmaViz warp map "hyperbolic_geometry" (mathematical): Hyperbolic space distortion
// From KarmaViz 67e8a15 warp_maps/mathematical/hyperbolic_geometry.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Hyperbolic Geometry Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    float dist = length(p);
    
    // Hyperbolic transformation
    float hyperbolic_factor = tanh(dist * 3.0 + sin(t * 0.5) * 0.5);
    
    // Poincaré disk model
    vec2 hyperbolic_pos = p * hyperbolic_factor / (1.0 + hyperbolic_factor);
    
    return (hyperbolic_pos - p) * 0.5;
}
