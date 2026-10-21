// KarmaViz warp map "Consciousness Stream" (experimental): Neural network simulation with synaptic firing, thought waves, and dream-like distortions
// From KarmaViz 67e8a15 warp_maps/experimental/consciousness_stream.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Consciousness Stream
vec2 get_pattern(vec2 pos, float t) {
    // Neural network-like connections
    vec2 neuron_grid = pos * 8.0;
    vec2 neuron_id = floor(neuron_grid);
    vec2 neuron_local = fract(neuron_grid);

    // Synaptic firing patterns
    float firing_rate = sin(neuron_id.x * 2.3 + neuron_id.y * 3.7 + t * 2.0) * 0.5 + 0.5;
    float activation = smoothstep(0.3, 0.7, firing_rate);

    // Thought propagation waves
    vec2 thought_center = vec2(0.3 + sin(t * 0.4) * 0.2, 0.7 + cos(t * 0.3) * 0.2);
    float thought_dist = length(pos - thought_center);
    float thought_wave = sin(thought_dist * 15.0 - t * 4.0) * exp(-thought_dist * 2.0);

    // Memory fragments (random access patterns)
    float memory_hash = fract(sin(dot(neuron_id, vec2(12.9898, 78.233))) * 43758.5453);
    float memory_access = step(0.95, memory_hash + sin(t * 1.5) * 0.1);

    // Consciousness flow field
    vec2 flow = vec2(
        sin(pos.y * 6.0 + t * 1.2) + thought_wave,
        cos(pos.x * 5.0 + t * 0.8) + activation * 0.5
    );

    // Dream-like distortions
    float dream_intensity = sin(t * 0.2) * 0.5 + 0.5;
    vec2 dream_warp = vec2(
        sin(pos.x * 20.0 + pos.y * 15.0 + t * 3.0),
        cos(pos.x * 12.0 - pos.y * 18.0 + t * 2.5)
    ) * dream_intensity * 0.02;

    return flow * 0.03 + dream_warp + vec2(memory_access * 0.01);
}
