// KarmaViz waveform "floating_orbs" (basic): Smooth floating orbs that dance to the rhythm
// From KarmaViz 67e8a15 waveforms/basic/floating_orbs.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Floating Orbs
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    for (int i = 0; i < 12; i++) {
        float fi = float(i);
        float fft = texture(fft_data, vec2(fi / 12.0, 0.5)).r;
        float ox = 0.5 + sin(time * 0.8 + fi * 3.1) * 0.3;
        float oy = 0.5 + cos(time * 0.6 + fi * 0.7) * 0.25;
        float size = 0.089 + fft * 0.5;
        float d = distance(vec2(x_coord, y_coord), vec2(ox, oy));
        if (d < size) {
            float intensity = (1.0 - d / size) * (0.3 + fft * 0.7);
            total += intensity * intensity;
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
