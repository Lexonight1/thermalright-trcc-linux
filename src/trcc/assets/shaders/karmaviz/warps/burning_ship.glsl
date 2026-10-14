// KarmaViz warp map "Burning Ship" (fractal): The Burning Ship fractal with its characteristic ship-like structures and fiery distortions
// From KarmaViz 67e8a15 warp_maps/fractal/burning_ship.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Burning Ship fractal coordinates
    vec2 c = (pos - vec2(0.3, 0.7)) * 3.0;
    
    // Animate the viewing window
    float zoom = 1.0 + sin(t * 0.2) * 0.5;
    c = c / zoom + vec2(-1.8, -0.08);
    
    vec2 z = vec2(0.0);
    float iterations = 0.0;
    float max_iterations = 10.0;
    
    // Burning Ship iteration with absolute values
    for (float i = 0.0; i < max_iterations; i++) {
        if (dot(z, z) > 4.0) break;
        
        // Key difference: take absolute value before squaring
        z = vec2(abs(z.x), abs(z.y));
        z = vec2(
            z.x * z.x - z.y * z.y + c.x,
            2.0 * z.x * z.y + c.y
        );
        
        iterations += 1.0;
    }
    
    // Create ship-like flow patterns
    float escape_value = iterations / max_iterations;
    
    // Generate flame-like distortions
    vec2 flame_direction;
    if (escape_value < 0.2) {
        // Inside - create upward flame motion
        float flame_angle = t * 2.0 + pos.x * 8.0;
        flame_direction = vec2(
            sin(flame_angle) * 0.3,
            cos(flame_angle) + 1.0  // Bias upward
        );
    } else {
        // Outside - create turbulent escape
        float turbulence_angle = escape_value * 6.28 + t * 1.5;
        flame_direction = vec2(
            cos(turbulence_angle),
            sin(turbulence_angle)
        ) * (1.0 - escape_value);
    }
    
    // Add ship wake effect
    float wake_strength = exp(-abs(pos.y - 0.5) * 8.0);
    vec2 wake_flow = vec2(sin(t + pos.x * 10.0) * wake_strength, 0.0);
    
    return (flame_direction + wake_flow) * 0.025;
}
