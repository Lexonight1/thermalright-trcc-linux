// KarmaViz warp map "crt_curvature" (retro): Classic CRT monitor screen curvature
// From KarmaViz 67e8a15 warp_maps/retro/crt_curvature.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// CRT Curvature Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    
    // CRT curvature
    float curvature_x = offset.x * offset.x * 0.1;
    float curvature_y = offset.y * offset.y * 0.08;
    
    // Slight barrel distortion
    float barrel = length(offset) * 0.05;
    
    // Animate slightly for that old TV feel
    float flicker = sin(t * 60.0) * 0.001;
    
    return vec2(curvature_x + flicker, curvature_y) * offset;
}
