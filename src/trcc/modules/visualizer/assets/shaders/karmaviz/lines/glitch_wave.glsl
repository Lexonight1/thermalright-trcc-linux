// KarmaViz waveform "glitch_wave" (digital): Digital glitch effects with random artifacts
// From KarmaViz 67e8a15 waveforms/digital/glitch_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Glitch effects
    float glitch_time = floor(time * 10.0);
    float glitch_rand = fract(sin(glitch_time * 12.9898 + norm_x * 78.233) * 43758.5453);

    // Random bit corruption
    if (glitch_rand > 0.95) {
        sample = sample * -1.0 + fract(sin(norm_x * 1000.0) * 43758.5453) * 0.5;
    }

    // Quantize with glitch
    float quantized = floor(sample * 16.0 + glitch_rand * 2.0) / 16.0;
    float waveform_value = quantized * waveform_scale;
    return waveform_value;
}
