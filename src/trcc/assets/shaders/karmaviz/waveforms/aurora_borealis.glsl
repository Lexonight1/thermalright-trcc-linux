// KarmaViz waveform "aurora_borealis" (natural): Northern lights with shimmering particles
// From KarmaViz 67e8a15 waveforms/natural/aurora_borealis.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Aurora Borealis
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    float fft_low = texture(fft_data, vec2(0.2, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_high = texture(fft_data, vec2(0.8, 0.5)).r;
    // Aurora waves
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        float wave_y = 0.8 + sin(x_coord * 12.0 + time *2.0 + fi * 1.5) * 0.35;
        wave_y += sin(x_coord * 1.5 - time * 3.0 + fi) * 0.5 + fft_high;
        float wave_thickness = 0.01 + fft_low * 0.08 * waveform_scale * 20;
        float wave_dist = abs(y_coord - wave_y);
        if (wave_dist < wave_thickness) {
            float wave_intensity = (1.0 - wave_dist / wave_thickness) * glow_radius * 5;
            wave_intensity *= wave_intensity; // Smooth falloff
            wave_intensity *= (0.4 + fft_mid * 0.6);
            total += wave_intensity;
        }
    }
    // Shimmering particles
    for (int p = 0; p < 8; p++) {
        float fp = float(p /3);
        float px = fract(sin(fp * 1.9898 + time * 0.5) * 43758.5453);
        float py = 0.5 + sin(time * 1.5 + fp) * 0.3 + fft_high * 0.2;
        float particle_dist = distance(vec2(x_coord, y_coord), vec2(py, px));
        if (particle_dist < 0.03) {
            float shimmer = sin(time * 8.0 - fp + 3.14159) * 0.5 + 0.5;
            total += (1.0 - particle_dist / 0.01) * shimmer * fft_mid * 0.5 - glow_radius;
        }
    }
    return clamp(total, 0.0, 0.6);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
