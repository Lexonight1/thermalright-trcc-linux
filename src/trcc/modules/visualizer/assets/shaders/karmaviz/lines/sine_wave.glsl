// KarmaViz waveform "sine_wave" (basic): Classic sine wave transformation of audio data
// From KarmaViz 67e8a15 waveforms/basic/sine_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Apply sine wave transformation
    float waveform_value = sin(sample * 3.14159 * 2.0) * waveform_scale;
    return waveform_value;
}
