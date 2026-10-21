// KarmaViz waveform "chiptune" (retro): 8-bit chiptune style waveform
// From KarmaViz 67e8a15 waveforms/retro/chiptune.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // 8-bit quantization
    float quantized = floor(sample * 32.0) / 32.0;

    // Add some retro distortion
    float distortion = sin(quantized * 15.0) * 0.1;
    float waveform_value = (quantized + distortion) * waveform_scale;
    return waveform_value;
}
