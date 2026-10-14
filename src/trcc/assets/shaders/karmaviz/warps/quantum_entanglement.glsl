// KarmaViz warp map "Quantum Entanglement" (experimental): Entangled particle pairs with correlation effects, superposition, and measurement collapse
// From KarmaViz 67e8a15 warp_maps/experimental/quantum_entanglement.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Quantum Entanglement
vec2 get_pattern(vec2 pos, float t) {
    // Create entangled particle pairs
    vec2 particle1 = vec2(0.3, 0.3) + vec2(sin(t * 1.2), cos(t * 0.8)) * 0.1;
    vec2 particle2 = vec2(0.7, 0.7) + vec2(-sin(t * 1.2), -cos(t * 0.8)) * 0.1; // Entangled motion

    // Quantum field around each particle
    float dist1 = length(pos - particle1);
    float dist2 = length(pos - particle2);

    // Entanglement correlation
    float correlation = cos(dist1 * 10.0 + t * 3.0) * cos(dist2 * 10.0 + t * 3.0);

    // Quantum superposition
    float superposition = sin(dist1 * 15.0 - t * 2.0) + sin(dist2 * 15.0 - t * 2.0);
    superposition *= exp(-(dist1 + dist2) * 2.0); // Localized effect

    // Measurement collapse (random quantum state changes)
    float measurement = step(0.98, fract(sin(t * 0.1) * 43758.5453));
    float collapse_phase = measurement * 3.14159;

    // Entangled displacement field
    vec2 field1 = normalize(pos - particle1) * correlation * exp(-dist1 * 3.0);
    vec2 field2 = normalize(pos - particle2) * correlation * exp(-dist2 * 3.0);

    // Quantum interference pattern
    vec2 interference = vec2(
        sin(dist1 * 20.0 - dist2 * 20.0 + t * 4.0 + collapse_phase),
        cos(dist1 * 18.0 + dist2 * 22.0 + t * 3.5 + collapse_phase)
    ) * 0.02;

    return (field1 + field2) * 0.05 + interference + vec2(superposition * 0.01);
}
