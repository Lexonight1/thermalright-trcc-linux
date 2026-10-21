// KarmaViz waveform "Pendulum Swing" (motion): Multiple pendulums swinging in harmony
// From KarmaViz 67e8a15 waveforms/motion/pendulum_swing.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Pendulum Swing
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        float fft = texture(fft_data, vec2(fi / 5.0, 0.5)).r;
        float pendulum_length = 0.3 + fi * 0.05;
        float swing_speed = 2.0 + fft * 3.0;
        float max_angle = 0.8 + fft * 1.0;
        float angle = sin(time * swing_speed + fi) * max_angle;
        vec2 anchor = vec2(0.2 + fi * 0.15, 0.1);
        vec2 bob_pos = anchor + vec2(sin(angle), cos(angle)) * pendulum_length;
        // Pendulum bob
        float bob_dist = distance(vec2(x_coord, y_coord), bob_pos);
        if (bob_dist < 0.02) {
            total += (1.0 - bob_dist / 0.02) * (0.6 + fft * 0.4);
        }
        // Pendulum string
        vec2 string_vec = bob_pos - anchor;
        float string_len = length(string_vec);
        vec2 string_dir = string_vec / string_len;
        float t = dot(vec2(x_coord, y_coord) - anchor, string_dir) / string_len;
        if (t >= 0.0 && t <= 1.0) {
            vec2 closest = anchor + t * string_vec;
            float string_dist = distance(vec2(x_coord, y_coord), closest);
            if (string_dist < 0.003) {
                total += 0.3;
            }
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
