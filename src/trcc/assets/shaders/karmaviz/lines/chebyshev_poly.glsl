// KarmaViz waveform "chebyshev_poly" (mathematical): Chebyshev polynomial waveshaping
// From KarmaViz 67e8a15 waveforms/mathematical/chebyshev_poly.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Chebyshev polynomial of 3rd order: 4x³ - 3x
    float x = clamp(sample, -1.0, 1.0);
    float cheby = 4.0 * x * x * x - 3.0 * x;

    float waveform_value = cheby * waveform_scale;
    return waveform_value;
}
