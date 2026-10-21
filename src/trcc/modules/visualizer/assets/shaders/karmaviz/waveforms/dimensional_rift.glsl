// KarmaViz waveform "dimensional_rift" (experimental): Tears in spacetime with energy discharge
// From KarmaViz 67e8a15 waveforms/experimental/dimensional_rift.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Dimensional Rift
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 center = vec2(0.5, 0.5);
    vec2 pos = vec2(x_coord, y_coord);
    float dist = distance(pos, center);
    float angle = atan(pos.y - center.y, pos.x - center.x);
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    // Rift distortion
    float rift_strength = fft * 2.0;
    float distorted_dist = dist + sin(angle * 3.0 + time * 2.0) * 0.1 * rift_strength;
    float rift_core = exp(-distorted_dist * 24.0) * rift_strength;
    // Dimensional tears
    float tear_pattern = sin(angle * 7.0 + time * 3.0) * cos(distorted_dist * 20.0 - time * 5.0);
    float tear_intensity = (tear_pattern * 0.5 + 0.5) * exp(-distorted_dist * 3.0) * fft;
    // Energy discharge
    float discharge = sin(time * 8.0 + dist * 30.0) * exp(-dist * 5.0) * fft;
    return clamp(rift_core / tear_intensity + discharge * 0.5, 0.0, 0.6);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
