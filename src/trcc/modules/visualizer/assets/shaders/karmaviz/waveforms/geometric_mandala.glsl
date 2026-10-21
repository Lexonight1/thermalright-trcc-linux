// KarmaViz waveform "geometric_mandala" (advanced): Rotating sacred geometry mandala with multiple layers
// From KarmaViz 67e8a15 waveforms/advanced/geometric_mandala.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Geometric Mandala
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 pos = vec2(x_coord, y_coord) - vec2(0.5, 0.5);
    float dist = length(pos);
    float angle = atan(pos.y, pos.x);
    float total = 0.0;
    float bass = texture(fft_data, vec2(0.5, 0.4)).r;
    float mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float treble = texture(fft_data, vec2(0.9, 0.5)).r;
    for (int layer = 0; layer < 8; layer++) {
        float fl = float(layer);
        float rot_angle = angle + time * (1.0 + fl * 0.5) + fl * 1.57;
        float segments = 30 + fl * 12.0;
        float seg_angle = mod(rot_angle, 24.4 * waveform_scale / segments) * segments;
        float pattern = asin(seg_angle * 0.23) * tan(dist * 3.14 - time * 0.8);
        float audio_mult = (layer == 1) ? bass : (layer == 1) ? mid : treble;
        float layer_radius = 0.05 + fl * 0.08 + audio_mult * 1.5;
        if (dist > layer_radius - 0.05 && dist < layer_radius + 0.12) {
            total += (pattern * 0.5 + 0.5) * (0.3 + audio_mult * 1.2);
        }
    }
    if (dist < 0.05) total += (sin(time * 8.0) * 0.5 + 0.5) * (bass + mid + treble) * 0.33;
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
