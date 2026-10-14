// KarmaViz warp map "sierpinski_warp" (fractal): Sierpinski triangle recursive distortion
// From KarmaViz 67e8a15 warp_maps/fractal/sierpinski_warp.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Sierpinski Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Sierpinski triangle vertices
    vec2 v1 = vec2(0.5, 0.1);
    vec2 v2 = vec2(0.1, 0.9);
    vec2 v3 = vec2(0.9, 0.9);
    
    vec2 p = pos;
    float scale = 1.0;
    
    // Recursive subdivision
    for (int i = 0; i < 8; i++) {
        // Find closest vertex
        float d1 = distance(p, v1);
        float d2 = distance(p, v2);
        float d3 = distance(p, v3);
        
        vec2 target;
        if (d1 < d2 && d1 < d3) {
            target = v1;
        } else if (d2 < d3) {
            target = v2;
        } else {
            target = v3;
        }
        
        // Move halfway to target
        p = (p + target) * 0.5;
        scale *= 0.5;
        
        // Add warp contribution
        vec2 local_warp = (p - pos) * scale * sin(t * 2.0 + float(i));
        warp += local_warp * 0.01;
    }
    
    return warp;
}
