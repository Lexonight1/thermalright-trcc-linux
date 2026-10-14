// KarmaViz waveform "wind_patterns" (organic): Natural wind flow patterns
// From KarmaViz 67e8a15 waveforms/organic/wind_patterns.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Wind turbulence
    float wind = sin(norm_x * 3.14159 + time * 0.7) * 0.3;
    wind += sin(norm_x * 9.42477 + time * 1.3) * 0.2;
    wind += sin(norm_x * 15.708 + time * 2.1) * 0.1;

    float waveform_value = sample * (1.0 + wind) * waveform_scale;
    return waveform_value;
}
