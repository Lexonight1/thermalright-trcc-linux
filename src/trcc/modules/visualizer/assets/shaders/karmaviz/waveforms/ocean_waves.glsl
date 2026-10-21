// KarmaViz waveform "Ocean Waves" (natural): Realistic ocean waves with foam and spray
// From KarmaViz 67e8a15 waveforms/natural/ocean_waves.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ocean Waves
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float fft = texture(fft_data, vec2(x_coord, 0.5)).r;
    float wave_height = 0.5;
    // Multiple wave layers
    wave_height += sin(x_coord * 6.0 + time * 2.0) * 0.1;
    wave_height += sin(x_coord * 12.0 + time * 3.0) * 0.05 * fft;
    wave_height += sin(x_coord * 20.0 + time * 4.0) * 0.03;
    wave_height += sin(x_coord * 35.0 + time * 6.0) * 0.02 * fft;
    float wave_dist = abs(y_coord - wave_height);
    float intensity = 0.0;
    // Main wave surface
    if (wave_dist < 0.02) {
        intensity = (1.0 - wave_dist / 0.02) * (0.6 + fft * 0.4);
    }
    // Foam and spray
    if (y_coord > wave_height && y_coord < wave_height + 0.1) {
        float foam_noise = sin(x_coord * 50.0 + time * 10.0) * sin(y_coord * 30.0 + time * 8.0);
        if (foam_noise > 0.3) {
            intensity += (foam_noise - 0.3) * 0.5 * fft;
        }
    }
    return clamp(intensity, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
