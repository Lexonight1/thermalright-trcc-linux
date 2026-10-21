// KarmaViz waveform "gravitational_wave" (cosmic): Gravitational wave ripples in spacetime
// From KarmaViz 67e8a15 waveforms/cosmic/gravitational_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Gravitational wave chirp
    float frequency = 35.0 + abs(sample) * 200.0; // Hz range of LIGO detection
    float amplitude = abs(sample) * 1e-21; // Strain amplitude

    // Chirp pattern (frequency increases as masses spiral in)
    float chirp_rate = abs(sample) * 10.0;
    float instantaneous_freq = frequency * (1.0 + chirp_rate * time);

    // Plus and cross polarizations
    float h_plus = amplitude * sin(norm_x * instantaneous_freq * 6.28318 + time * instantaneous_freq);
    float h_cross = amplitude * cos(norm_x * instantaneous_freq * 6.28318 + time * instantaneous_freq) * 0.7;

    // Combine polarizations
    float gw_strain = h_plus + h_cross;

    float waveform_value = gw_strain * 1e18 * waveform_scale;
    return waveform_value;
}
