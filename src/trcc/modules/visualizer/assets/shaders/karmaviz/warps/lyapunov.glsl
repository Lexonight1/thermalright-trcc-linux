// KarmaViz warp map "Lyapunov" (fractal): Lyapunov fractal showing chaotic stability regions with dynamic parameter sequences
// From KarmaViz 67e8a15 warp_maps/fractal/lyapunov.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Lyapunov fractal parameters
    vec2 params = pos * 2.0 + vec2(2.0, 2.0); // Map to parameter space
    
    // Animate parameter space
    float param_drift = t * 0.1;
    params += vec2(sin(param_drift), cos(param_drift * 1.3)) * 0.2;
    
    float a = params.x;
    float b = params.y;
    
    // Lyapunov sequence (simplified AABAB pattern)
    float x = 0.5; // Starting value
    float lyapunov_sum = 0.0;
    int sequence_length = 8;
    
    // Calculate Lyapunov exponent
    for (int i = 0; i < sequence_length; i++) {
        float r;
        
        // Sequence pattern: AABAB...
        if (mod(float(i), 5.0) < 2.0 || mod(float(i), 5.0) == 3.0) {
            r = a;
        } else {
            r = b;
        }
        
        // Logistic map iteration: x = r * x * (1 - x)
        x = r * x * (1.0 - x);
        
        // Accumulate Lyapunov exponent
        if (x > 0.001 && x < 0.999) {
            lyapunov_sum += log(abs(r * (1.0 - 2.0 * x)));
        }
    }
    
    float lyapunov_exponent = lyapunov_sum / float(sequence_length);
    
    // Create flow based on stability
    vec2 stability_flow;
    
    if (lyapunov_exponent < 0.0) {
        // Stable region - create convergent flow
        vec2 attractor_center = vec2(0.5, 0.5);
        vec2 to_attractor = attractor_center - pos;
        stability_flow = normalize(to_attractor) * abs(lyapunov_exponent) * 2.0;
    } else {
        // Chaotic region - create divergent flow
        float chaos_angle = lyapunov_exponent * 2.0 + t * 1.5;
        stability_flow = vec2(
            cos(chaos_angle),
            sin(chaos_angle)
        ) * lyapunov_exponent;
    }
    
    // Add parameter-space turbulence
    float param_turbulence_phase = t * 2.0 + a * 3.0 + b * 4.0;
    vec2 param_turbulence = vec2(
        sin(param_turbulence_phase) * cos(param_turbulence_phase * 1.618),
        cos(param_turbulence_phase) * sin(param_turbulence_phase * 0.618)
    ) * 0.3;
    
    // Add sequence-based modulation
    float sequence_phase = t * 1.2 + pos.x * 5.0 + pos.y * 7.0;
    float sequence_modulation = sin(sequence_phase) * 0.2 + 1.0;
    
    // Combine effects
    vec2 total_flow = (stability_flow + param_turbulence) * sequence_modulation;
    
    // Scale by chaos intensity
    float chaos_intensity = smoothstep(-1.0, 1.0, lyapunov_exponent);
    
    return total_flow * (0.5 + chaos_intensity * 0.5) * 0.025;
}
