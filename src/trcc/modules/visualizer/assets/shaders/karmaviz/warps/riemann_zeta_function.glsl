// KarmaViz warp map "Riemann Zeta Function" (mathematical): Visualization of the Riemann zeta function with critical line and complex plane mapping
// From KarmaViz 67e8a15 warp_maps/mathematical/riemann_zeta_function.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Riemann Zeta Function Visualization
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    
    // Approximate Riemann zeta function for complex numbers
    vec2 s = vec2(0.5 + sin(t * 0.1) * 0.3, centered.x * 2.0);
    vec2 zeta_sum = vec2(0.0);
    
    // Partial sum approximation
    for (int n = 1; n <= 20; n++) {
        float n_float = float(n);
        // 1/n^s for complex s
        float n_pow_real = pow(n_float, -s.x) * cos(-s.y * log(n_float));
        float n_pow_imag = pow(n_float, -s.x) * sin(-s.y * log(n_float));
        zeta_sum += vec2(n_pow_real, n_pow_imag);
    }
    
    // Create displacement based on zeta function
    vec2 displacement = vec2(
        zeta_sum.x * sin(centered.y * 6.28318 + t),
        zeta_sum.y * cos(centered.x * 6.28318 + t)
    ) * 0.03;
    
    // Add critical line visualization (Re(s) = 1/2)
    float critical_line = abs(centered.x - 0.0) * 10.0;
    displacement += vec2(
        sin(critical_line + t * 2.0) * 0.01,
        cos(critical_line + t * 1.5) * 0.01
    );
    
    return displacement;
}
