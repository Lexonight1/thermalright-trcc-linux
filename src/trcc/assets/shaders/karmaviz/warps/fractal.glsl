// KarmaViz warp map "Fractal" (experimental): Complex fractal-based distortions with iterative mathematical patterns
// From KarmaViz 67e8a15 warp_maps/experimental/fractal.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Fractal-based distortion pattern
vec2 get_pattern(vec2 pos, float t) {
    vec2 z = (pos - 0.5) * 4.0;
    vec2 offset = vec2(0.0);
    
    // Simple fractal iteration
    for (int i = 0; i < 3; i++) {
        float r = length(z);
        float angle = atan(z.y, z.x) + t * 0.1;
        
        z = vec2(cos(angle) * r, sin(angle) * r);
        z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + vec2(sin(t * 0.3), cos(t * 0.2));
        
        offset += z * 0.01 / float(i + 1);
    }
    
    return offset * 0.5;
}
