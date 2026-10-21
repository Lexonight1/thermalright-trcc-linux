// KarmaViz waveform "mandelbrot_wave" (fractal): Waveform modulated by Mandelbrot-like iterations
// From KarmaViz 67e8a15 waveforms/fractal/mandelbrot_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Simple fractal-like iteration
    float z = norm_x * 2.0 - 1.0;
    float c = sample * 0.5;
    for (int i = 0; i < 5; i++) {
        z = z * z + c;
        if (abs(z) > 2.0) break;
    }

    float fractal_mod = clamp(abs(z) / 2.0, 0.0, 1.0);
    float waveform_value = sample * fractal_mod * waveform_scale;
    return waveform_value;
}
