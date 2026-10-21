// KarmaViz warp map "Fourier Transform" (mathematical): Visualization of Fourier series with multiple frequency components and harmonic interactions
// From KarmaViz 67e8a15 warp_maps/mathematical/fourier_transform.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Fourier Transform Visualization
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Multiple frequency components
    vec2 displacement = vec2(0.0);

    // Fundamental frequency and harmonics
    for (int i = 1; i <= 5; i++) {
        float freq = float(i);
        float amplitude = 1.0 / freq;  // Decreasing amplitude for harmonics

        // X-direction Fourier components
        displacement.x += amplitude * sin(freq * 2.0 * 3.14159 * pos.x + t * freq * 0.5) * 0.02;
        displacement.y += amplitude * cos(freq * 2.0 * 3.14159 * pos.y + t * freq * 0.3) * 0.02;

        // Cross-frequency interactions
        displacement += vec2(
            sin(freq * pos.x * 8.0 + t) * cos(freq * pos.y * 6.0 + t),
            cos(freq * pos.x * 6.0 + t) * sin(freq * pos.y * 8.0 + t)
        ) * amplitude * 0.01;
    }

    // Add phase modulation
    float phase_mod = sin(length(centered) * 10.0 + t * 2.0) * 0.5;
    displacement *= 1.0 + phase_mod;

    return displacement;
}
