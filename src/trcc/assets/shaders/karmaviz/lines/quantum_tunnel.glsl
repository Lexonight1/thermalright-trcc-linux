// KarmaViz waveform "quantum_tunnel" (experimental): Quantum tunneling probability wave
// From KarmaViz 67e8a15 waveforms/experimental/quantum_tunnel.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Quantum tunneling barrier
    float barrier_center = 0.5;
    float barrier_width = 0.2;
    float barrier_height = 2.0 + abs(sample) * 3.0;

    float distance_to_barrier = abs(norm_x - barrier_center);
    float tunnel_prob = exp(-barrier_height * max(0.0, barrier_width - distance_to_barrier));

    float waveform_value = sample * tunnel_prob * waveform_scale;
    return waveform_value;
}
