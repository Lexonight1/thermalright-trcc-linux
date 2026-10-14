// KarmaViz waveform "fourier_series" (mathematical): Fourier series approximation of square wave
// From KarmaViz 67e8a15 waveforms/mathematical/fourier_series.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Fourier series approximation
    float fourier = 0.0;
    for (int n = 1; n <= 7; n += 2) {
        float fn = float(n);
        fourier += sin(fn * sample * 6.28318) / fn;
    }
    fourier *= 4.0 / 3.14159;

    float waveform_value = fourier * waveform_scale;
    return waveform_value;
}
