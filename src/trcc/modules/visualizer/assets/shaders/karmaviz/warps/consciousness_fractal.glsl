// KarmaViz warp map "Consciousness Fractal" (experimental): Recursive consciousness simulation with awareness, focus, memory, and meditation states
// From KarmaViz 67e8a15 warp_maps/experimental/consciousness_fractal.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Consciousness Fractal
vec2 get_pattern(vec2 pos, float t) {
    vec2 z = (pos - 0.5) * 2.0;
    vec2 displacement = vec2(0.0);

    // Consciousness parameters
    float awareness = sin(t * 0.2) * 0.5 + 0.5;
    float focus = cos(t * 0.15) * 0.3 + 0.7;

    // Multi-scale consciousness iteration
    for (int i = 0; i < 6; i++) {
        float scale = pow(2.0, float(i));
        vec2 scaled_z = z * scale;

        // Thought pattern generation
        vec2 thought = vec2(
            sin(scaled_z.x + t * (1.0 + float(i) * 0.3)) * awareness,
            cos(scaled_z.y + t * (0.8 + float(i) * 0.2)) * focus
        );

        // Memory consolidation (fractal self-similarity)
        float memory_strength = exp(-float(i) * 0.5);
        vec2 memory_pattern = vec2(
            thought.x * thought.y,
            thought.x - thought.y
        ) * memory_strength;

        // Consciousness recursion
        z = vec2(
            z.x * z.x - z.y * z.y + memory_pattern.x * 0.1,
            2.0 * z.x * z.y + memory_pattern.y * 0.1
        );

        // Accumulate displacement with decreasing influence
        displacement += z * memory_strength * 0.01;

        // Prevent infinite growth
        if (length(z) > 2.0) break;
    }

    // Self-awareness feedback loop
    float self_awareness = sin(length(displacement) * 10.0 + t * 3.0) * awareness;
    displacement += vec2(self_awareness) * 0.005;

    // Meditation state (centering effect)
    float meditation = smoothstep(0.8, 1.0, awareness) * smoothstep(0.9, 1.0, focus);
    displacement *= 1.0 - meditation * 0.5;

    return displacement;
}
