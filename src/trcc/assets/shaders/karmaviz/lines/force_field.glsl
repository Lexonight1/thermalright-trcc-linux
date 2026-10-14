// KarmaViz waveform "force_field" (futuristic): Energy force field distortion
// From KarmaViz 67e8a15 waveforms/futuristic/force_field.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Force field distortion
    float field_strength = abs(sample);
    float distortion = sin(norm_x * 20.0 + time * 5.0) * field_strength * 0.3;
    float field_sample = texture(waveform_data, vec2(norm_x + distortion * 0.1, 0.5)).r;

    // Energy ripples
    float ripple = sin(norm_x * 15.0 - time * 8.0) * 0.2 + 0.8;

    float waveform_value = field_sample * ripple * waveform_scale;
    return waveform_value;
}
