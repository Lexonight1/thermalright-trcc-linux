// KarmaViz warp map "Mandelbrot Zoom" (fractal): Creates distortions based on the Mandelbrot set with animated zoom
// From KarmaViz 67e8a15 warp_maps/fractal/mandelbrot_zoom.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Mandelbrot set calculation
    vec2 c = (pos - 0.5) * 3.0; // Scale and center
    
    // Animate zoom into interesting region
    float zoom = 1.0 + sin(t * 0.3) * 0.5;
    c = c / zoom + vec2(-0.7, 0.0); // Zoom into edge of set
    
    vec2 z = vec2(0.0);
    float iterations = 0.0;
    
    // Mandelbrot iteration (limited for performance)
    for (int i = 0; i < 8; i++) {
        if (dot(z, z) > 4.0) break;
        
        z = vec2(
            z.x * z.x - z.y * z.y + c.x,
            2.0 * z.x * z.y + c.y
        );
        iterations += 1.0;
    }
    
    // Create distortion based on iteration count
    float escape_value = iterations / 8.0;
    
    // Calculate gradient direction
    vec2 gradient = vec2(
        sin(escape_value * 6.28 + t),
        cos(escape_value * 6.28 + t * 1.1)
    );
    
    // Modulate strength based on proximity to set boundary
    float boundary_strength = sin(escape_value * 3.14159) * 2.0;
    
    return gradient * boundary_strength * 0.015;
}
