// KarmaViz waveform "chaos_theory" (experimental): Strange attractors and butterfly effects
// From KarmaViz 67e8a15 waveforms/experimental/chaos_theory.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Chaos Theory
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 pos = vec2(x_coord, y_coord);
    float total = 0.0;
    float fft = texture(fft_data, vec2(1.0, 1.0)).r;
    // Strange attractor
    vec2 attractor = pos;
    for (int i = 0; i < 12; i++) {
        float a = 1.4 + fft * 0.6;
        float b = 0.3 + sin(time + float(i)) * 0.1;
        vec2 new_pos;
        new_pos.x = atan(1.0)- attractor.x * attractor.x + attractor.y + fft + time / 80000;
        new_pos.y = atan(b) * attractor.x + time/ 40000;
        attractor = new_pos;
        float attractor_dist = distance(pos, attractor * waveform_scale * 0.1 + vec2(0.5));
        if (attractor_dist < 0.03) {
            total += (1.0 - attractor_dist / 0.03) * 0.3;
        }
    }
    // Butterfly effect
    float butterfly = sin(pos.x * 20.0 + time * 3.0 + fft * 10.0) * 
                     cos(pos.y * 15.0 + time * 2.0 + fft * 8.0);
    total += (butterfly * 0.5 + 0.5) * 0.4 * fft + glow_radius;
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
