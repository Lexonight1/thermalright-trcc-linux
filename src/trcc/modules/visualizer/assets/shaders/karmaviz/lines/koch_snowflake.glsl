// KarmaViz waveform "koch_snowflake" (fractal): Koch snowflake edge pattern
// From KarmaViz 67e8a15 waveforms/fractal/koch_snowflake.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Koch snowflake edge approximation
    float koch = 0.0;
    float scale = 1.0;

    for (int i = 0; i < 6; i++) {
        float segment = fract(norm_x * scale);
        if (segment < 0.33) {
            koch += sin(segment * 9.42477) * (1.0 / scale);
        } else if (segment < 0.67) {
            koch += sin((segment - 0.33) * 9.42477 + 2.094) * (1.0 / scale);
        } else {
            koch += sin((segment - 0.67) * 9.42477) * (1.0 / scale);
        }
        scale *= 3.0;
    }

    float waveform_value = sample * (1.0 + koch * abs(sample)) * waveform_scale;
    return waveform_value;
}
