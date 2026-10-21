// KarmaViz warp map "Quantum Tunneling" (experimental): Quantum mechanics-inspired effect with probability waves, tunneling, and uncertainty principle
// From KarmaViz 67e8a15 warp_maps/experimental/quantum_tunneling.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Quantum Tunneling Effect
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Create quantum probability waves
    float wave1 = sin(pos.x * 20.0 + t * 3.0) * cos(pos.y * 15.0 + t * 2.5);
    float wave2 = cos(pos.x * 12.0 - t * 2.0) * sin(pos.y * 18.0 - t * 3.5);
    float wave3 = sin(pos.x * 25.0 + pos.y * 22.0 + t * 4.0);

    // Quantum interference pattern
    float interference = (wave1 + wave2 + wave3) / 3.0;

    // Tunneling probability based on distance from center
    float radius = length(centered);
    float tunnel_probability = exp(-radius * 3.0) * (0.5 + interference * 0.5);

    // Quantum displacement with uncertainty principle
    vec2 uncertainty = vec2(
        sin(t * 7.0 + pos.x * 30.0) * 0.02,
        cos(t * 6.0 + pos.y * 25.0) * 0.02
    );

    // Phase transition zones
    float phase = sin(radius * 8.0 + t * 2.0) * 0.5 + 0.5;
    vec2 phase_shift = centered * phase * tunnel_probability * 0.3;

    return phase_shift + uncertainty;
}
