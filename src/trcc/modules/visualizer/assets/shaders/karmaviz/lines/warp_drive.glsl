// KarmaViz waveform "warp_drive" (futuristic): Warp drive energy signature
// From KarmaViz 67e8a15 waveforms/futuristic/warp_drive.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Warp field compression
    float warp_factor = 1.0 + abs(sample) * 2.0;
    float compressed_x = 0.5 + (norm_x - 0.5) / warp_factor;
    compressed_x = clamp(compressed_x, 0.0, 1.0);

    float warp_sample = texture(waveform_data, vec2(compressed_x, 0.5)).r;

    // Subspace harmonics
    float harmonics = sin(norm_x * 30.0 + time * 10.0) * 0.1;

    float waveform_value = warp_sample * (1.0 + harmonics) * waveform_scale;
    return waveform_value;
}
