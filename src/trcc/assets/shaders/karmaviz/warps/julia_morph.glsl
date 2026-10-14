// KarmaViz warp map "julia_morph" (fractal): Morphing Julia set creating dynamic fractal distortions
// From KarmaViz 67e8a15 warp_maps/fractal/julia_morph.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Julia set with animated parameter
    t = time;
    vec2 z = (pos - 0.5) * 2.5;
    
    // Animate the Julia set parameter
    vec2 c = vec2(
        cos(t * 0.4) * 0.7,
        sin(t * 0.3) * 0.7
    );
    
    float iterations = 0.0;
    vec2 dz = vec2(1.0, 0.0); // Derivative for distance estimation
    
    // Julia set iteration with derivative
    for (int i = 0; i < 6; i++) {
        if (dot(z, z) > 5.0) break;
        
        // Update derivative: dz = 2 * z * dz
        dz = vec2(
            2.0 * (z.x * dz.x - z.y * dz.y),
            2.0 * (z.x * dz.y + z.y * dz.x)
        );
        
        // Update z: z = z^2 + c
        z = vec2(
            z.x * z.x - z.y * z.y + c.x,
            2.0 * z.x * z.y + c.y
        );
        
        iterations += 1.0;
    }
    
    // Distance estimation
    float distance_est = length(z) * log(length(z)) / length(dz);
    
    // Create distortion based on distance to fractal boundary
    float boundary_factor = exp(-distance_est * 20.0);
    
    // Direction based on gradient
    vec2 direction = normalize(dz);
    
    return direction * boundary_factor * 0.02;
}
