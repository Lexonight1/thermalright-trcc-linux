// KarmaViz waveform "dragon_curve" (fractal): Dragon curve fractal pattern
// From KarmaViz 67e8a15 waveforms/fractal/dragon_curve.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Dragon curve approximation
    float dragon = 0.0;
    float x = norm_x * 4.0 - 2.0;

    for (int i = 0; i < 8; i++) {
        float scale = pow(0.7, float(i));
        float offset = sin(float(i) * 2.1) * scale;
        dragon += sin(x / scale + offset + time * 0.5) * scale;
    }

    float waveform_value = sample * (1.0 + dragon * 0.3) * waveform_scale;
    return waveform_value;
}
