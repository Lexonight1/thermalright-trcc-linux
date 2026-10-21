// KarmaViz waveform "Ripple Rings" (basic): Concentric rings that ripple and expand with the music
// From KarmaViz 67e8a15 waveforms/basic/ripple_rings.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ripple Rings
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 center = vec2(0.5, 0.5);
    float dist = distance(vec2(x_coord, y_coord), center);
    float total = 0.0;
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float fft = texture(fft_data, vec2(fi / 6.0, 0.5)).r;
        float radius = 0.1 + fi * 0.08 + sin(time * 2.0 + fi) * 0.02;
        float width = 0.015 + fft * 0.02;
        float anim_radius = radius + sin(time * (1.0 + fft * 2.0) + fi * 2.0) * 0.03;
        float ring_dist = abs(dist - anim_radius);
        if (ring_dist < width) {
            total += (1.0 - ring_dist / width) * (0.4 + fft * 0.8);
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
