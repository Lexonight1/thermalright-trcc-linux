// KarmaViz waveform "logarithmic" (mathematical): Logarithmic scaling of waveform amplitude
// From KarmaViz 67e8a15 waveforms/mathematical/logarithmic.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Apply logarithmic scaling
    float abs_sample = abs(sample);
    float log_sample = log(1.0 + abs_sample * 10.0) / log(11.0);
    float waveform_value = log_sample * sign(sample) * waveform_scale;
    return waveform_value;
}
