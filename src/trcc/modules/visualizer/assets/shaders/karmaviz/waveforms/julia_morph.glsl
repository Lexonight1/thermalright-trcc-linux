// KarmaViz waveform "julia_morph" (fractal): Morphing Julia sets with audio control
// From KarmaViz 67e8a15 waveforms/fractal/julia_morph.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Julia Morph
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    vec2 c = vec2(sin(time * 0.5 + fft * 3.0) * 0.8, cos(time * 0.3 + fft * 2.0) * 0.8);
    vec2 z = (vec2(x_coord, y_coord) - vec2(0.5)) * 3.0;
    float iterations = 0.0;
    for (int i = 0; i < 15; i++) {
        if (dot(z, z) > 4.0) break;
        z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + c;
        iterations += 1.0;
    }
    float intensity = iterations / 15.0;
    return intensity * (0.6 + fft * 0.4);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
