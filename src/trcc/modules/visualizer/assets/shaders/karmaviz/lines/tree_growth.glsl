// KarmaViz waveform "tree_growth" (organic): Organic tree growth pattern
// From KarmaViz 67e8a15 waveforms/organic/tree_growth.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Tree growth branching
    float trunk = exp(-abs(norm_x - 0.5) * 8.0);
    float branch1 = exp(-abs(norm_x - 0.3) * 12.0) * step(0.2, abs(sample));
    float branch2 = exp(-abs(norm_x - 0.7) * 12.0) * step(0.2, abs(sample));

    float growth = trunk + branch1 + branch2;
    float waveform_value = sample * growth * waveform_scale;
    return waveform_value;
}
