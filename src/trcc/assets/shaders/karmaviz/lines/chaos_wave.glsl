// KarmaViz waveform "chaos_wave" (experimental): Chaotic transformation with unpredictable behavior
// From KarmaViz 67e8a15 waveforms/experimental/chaos_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Chaotic transformation
    float chaos = fract(sin(norm_x * 12.9898 + time * 0.1) * 43758.5453);
    chaos = chaos * 2.0 - 1.0;

    float waveform_value = sample * (0.7 + chaos * 0.3) * waveform_scale;
    return waveform_value;
}
