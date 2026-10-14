// KarmaViz waveform "breathing" (organic): Organic breathing-like waveform modulation
// From KarmaViz 67e8a15 waveforms/organic/breathing.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Create breathing effect
    float breath = sin(time * 0.5) * 0.3 + 0.7;
    float organic_mod = sin(norm_x * 6.28318 + time * 2.0) * 0.1;
    float waveform_value = sample * breath * (1.0 + organic_mod) * waveform_scale;
    return waveform_value;
}
