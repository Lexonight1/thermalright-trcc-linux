// KarmaViz waveform "lightning_storm" (natural): Electric lightning bolts with rain
// From KarmaViz 67e8a15 waveforms/natural/lightning_storm.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Lightning Storm
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    // Lightning bolts
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        float bolt_trigger = sin(time * 5.0 + fi * 2.1) * 0.5 + 0.5;
        if (bolt_trigger > 0.7 && fft_high > 0.5) {
            float bolt_x = 0.2 + fi * 0.3;
            float bolt_path = bolt_x + sin(y_coord * 20.0 + time * 10.0 + fi) * 0.1;
            float bolt_dist = abs(x_coord - bolt_path);
            if (bolt_dist < 0.01) {
                float bolt_intensity = (1.0 - bolt_dist / 0.01) * fft_high * 2.0;
                total += bolt_intensity;
            }
            // Lightning glow
            if (bolt_dist < 0.05) {
                total += (1.0 - bolt_dist / 0.05) * 0.3 * fft_high;
            }
        }
    }
    // Rain drops
    for (int r = 0; r < 20; r++) {
        float fr = float(r);
        float drop_x = fract(sin(fr * 12.9898) * 43758.5453);
        float drop_speed = 2.0 + fft_mid * 3.0;
        float drop_y = 1.0 - fract(time * drop_speed + fr * 0.5);
        float drop_dist = distance(vec2(x_coord, y_coord), vec2(drop_x, drop_y));
        if (drop_dist < 0.005) {
            total += (1.0 - drop_dist / 0.005) * 0.4;
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
