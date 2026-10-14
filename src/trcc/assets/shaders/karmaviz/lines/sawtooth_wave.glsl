// KarmaViz waveform "sawtooth_wave" (basic): Sawtooth wave with sharp rising edges
// From KarmaViz 67e8a15 waveforms/basic/sawtooth_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Sawtooth transformation
    float sawtooth = 2.0 * fract(sample * 2.0) - 1.0;
    float waveform_value = sawtooth * waveform_scale;
    return waveform_value;
}
