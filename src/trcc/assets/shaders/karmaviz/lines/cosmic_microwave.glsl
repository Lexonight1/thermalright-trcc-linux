// KarmaViz waveform "cosmic_microwave" (cosmic): Cosmic microwave background radiation
// From KarmaViz 67e8a15 waveforms/cosmic/cosmic_microwave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // CMB temperature fluctuations
    float cmb_base = 2.725; // Kelvin
    float fluctuation = 0.0;

    // Multiple scales of fluctuation
    for (int i = 1; i <= 8; i++) {
        float scale = pow(2.0, float(i));
        float amplitude = 1.0 / scale;
        fluctuation += sin(norm_x * scale * 6.28318 + time * 0.1) * amplitude;
    }

    // Modulate by audio
    float cmb_signal = fluctuation * abs(sample) * 0.001; // Microkelvin scale

    float waveform_value = cmb_signal * 1000.0 * waveform_scale;
    return waveform_value;
}
