// KarmaViz waveform "triangle_wave" (basic): Triangle wave transformation with smooth transitions
// From KarmaViz 67e8a15 waveforms/basic/triangle_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Triangle wave transformation
    float triangle = 2.0 * abs(fract(sample * 2.0) - 0.5) - 0.5;
    float waveform_value = triangle * waveform_scale;
    return waveform_value;
}
