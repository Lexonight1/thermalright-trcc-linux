// KarmaViz waveform "Sierpinski Triangle" (fractal): Recursive triangle fractal pattern
// From KarmaViz 67e8a15 waveforms/fractal/sierpinski_triangle.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Sierpinski Triangle
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    vec2 pos = vec2(x_coord, y_coord);
    float scale = 1.0 + fft * 2.0;
    pos *= scale;
    float intensity = 0.0;
    for (int i = 0; i < 8; i++) {
        pos *= 2.0;
        vec2 fpos = fract(pos);
        if (fpos.x + fpos.y > 1.0) {
            intensity += 1.0 / pow(2.0, float(i + 1));
        }
        pos = fpos;
    }
    return intensity * (0.5 + fft * 0.5);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
