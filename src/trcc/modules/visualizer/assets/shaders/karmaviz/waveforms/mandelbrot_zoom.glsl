// KarmaViz waveform "mandelbrot_zoom" (fractal): Zooming into the Mandelbrot set
// From KarmaViz 67e8a15 waveforms/fractal/mandelbrot_zoom.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Mandelbrot Zoom
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    float zoom = 1.0 + time * 0.5 + fft * 2.0;
    vec2 c = (vec2(x_coord, y_coord) - vec2(0.5)) * zoom - vec2(0.5, 0.0);
    vec2 z = vec2(0.0);
    float iterations = 0.0;
    for (int i = 0; i < 20; i++) {
        if (dot(z, z) > 4.0) break;
        z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + c;
        iterations += 1.0;
    }
    float intensity = iterations / 20.0;
    return intensity * (0.5 + fft * 0.5);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
