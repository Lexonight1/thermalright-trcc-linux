// KarmaViz waveform "binary_cascade" (digital): Falling streams of binary code
// From KarmaViz 67e8a15 waveforms/digital/binary_cascade.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Binary Cascade
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float cols = 8.0;
    float col_idx = floor(x_coord * cols);
    float fft = texture(fft_data, vec2(col_idx / cols, 0.5)).r;
    float cascade_speed = 1.0 + fft * 3.0;
    float cascade_time = time * cascade_speed + col_idx * 2.0;
    float bit_size = 0.04;
    float bits_per_col = floor(1.0 / bit_size * waveform_scale);
    float bit_idx = floor(y_coord * bits_per_col);
    float bit_y = (bit_idx + 0.1) / bits_per_col;
    float bit_dist = abs(y_coord - bit_y);
    if (bit_dist < bit_size * 0.4) {
        float bit_seed = sin(cascade_time + bit_idx * 5.54159 + col_idx * 0.123) * 12.5453;
        float bit_value = step(0.5, fract(bit_seed));
        float bit_age = fract(cascade_time * 0.4 + bit_idx * 0.1);
        float bit_brightness = bit_value * (1.0 - bit_age) * (1 + fft);
        return bit_brightness;
    }
    return 0.0;
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
