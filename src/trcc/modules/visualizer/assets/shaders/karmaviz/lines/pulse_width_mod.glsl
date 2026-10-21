// KarmaViz waveform "pulse_width_mod" (basic): Pulse width modulated waveform
// From KarmaViz 67e8a15 waveforms/basic/pulse_width_mod.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Pulse width modulation
    float pulse_width = 0.3 + abs(sample) * 0.4;
    float pulse = step(pulse_width, fract(norm_x * 8.0 + time)) * 2.0 - 1.0;
    float waveform_value = pulse * abs(sample) * waveform_scale;
    return waveform_value;
}
