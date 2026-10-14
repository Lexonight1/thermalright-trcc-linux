// KarmaViz waveform "stepped_wave" (digital): Stepped digital waveform with quantized levels
// From KarmaViz 67e8a15 waveforms/digital/stepped_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Create stepped levels
    float waveform_value = floor(sample * 16.0) / 16.0 * waveform_scale;
    return waveform_value;
}
