// KarmaViz waveform "exponential" (mathematical): Exponential scaling emphasizing loud parts
// From KarmaViz 67e8a15 waveforms/mathematical/exponential.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Apply exponential scaling
    float abs_sample = abs(sample);
    float exp_sample = (exp(abs_sample * 2.0) - 1.0) / (exp(2.0) - 1.0);
    float waveform_value = exp_sample * sign(sample) * waveform_scale;
    return waveform_value;
}
