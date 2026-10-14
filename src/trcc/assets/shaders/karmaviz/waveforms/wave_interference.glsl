// KarmaViz waveform "wave_interference" (motion): Interfering waves from multiple sources
// From KarmaViz 67e8a15 waveforms/motion/wave_interference.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Wave Interference
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 pos = vec2(x_coord, y_coord);
    float total = 0.0;
    vec2 sources[4] = vec2[4](vec2(0.3, 0.3), vec2(0.7, 0.3), vec2(0.3, 0.7), vec2(0.7, 0.7));
    for (int i = 0; i < 4; i++) {
        float fft = texture(fft_data, vec2(float(i) / 4.0, 0.5)).r;
        float dist = distance(pos, sources[i]);
        float frequency = 108.0 + float(i) * 2.0 + fft * 10.0;
        float amplitude = 0.4 + fft * 0.4;
        float wave = sin(dist * frequency - time * 5.0) * amplitude * exp(-dist * .0);
        total += wave;
    }
    return clamp(total * 0.5 * 0.5, 0.0, 0.1);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
