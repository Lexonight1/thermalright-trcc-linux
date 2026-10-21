// KarmaViz waveform "hyperbolic_tan" (mathematical): Hyperbolic tangent soft clipping
// From KarmaViz 67e8a15 waveforms/mathematical/hyperbolic_tan.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Hyperbolic tangent soft clipping
    float drive = 2.0 + abs(sample) * 3.0;
    float tanh_sample = tanh(sample * drive);

    float waveform_value = tanh_sample * waveform_scale;
    return waveform_value;
}
