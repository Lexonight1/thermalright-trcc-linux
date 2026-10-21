// KarmaViz warp map "Gamma Function Complex" (mathematical): Complex gamma function with poles and Stirling approximation visualization
// From KarmaViz 67e8a15 warp_maps/mathematical/gamma_function_complex.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Gamma Function in Complex Plane
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    vec2 z = centered * 4.0; // Scale to interesting region
    
    // Stirling's approximation for Gamma function
    // Γ(z) ≈ √(2π/z) * (z/e)^z for large |z|
    float z_mag = length(z);
    float z_arg = atan(z.y, z.x);
    
    // Log-gamma approximation
    vec2 log_gamma = vec2(
        (z.x - 0.5) * log(z_mag) - z.x + 0.5 * log(6.28318),
        z.y * log(z_mag) - z.y - z_arg
    );
    
    // Poles at negative integers
    float pole_effect = 0.0;
    for (int n = 0; n < 5; n++) {
        float pole_dist = length(z - vec2(-float(n), 0.0));
        pole_effect += 1.0 / (pole_dist + 0.1);
    }
    
    // Create displacement
    vec2 displacement = vec2(
        sin(log_gamma.x + t) * cos(log_gamma.y),
        cos(log_gamma.x + t) * sin(log_gamma.y)
    ) * 0.02;
    
    // Add pole singularities
    displacement += vec2(
        sin(pole_effect + t * 3.0),
        cos(pole_effect + t * 2.0)
    ) * 0.01;
    
    return displacement;
}
