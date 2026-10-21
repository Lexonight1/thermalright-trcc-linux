// KarmaViz waveform "Neural Network" (advanced): Interconnected nodes with animated data flow
// From KarmaViz 67e8a15 waveforms/advanced/neural_network.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Neural Network
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 nodes[8] = vec2[8](vec2(0.2,0.3), vec2(0.8,0.3), vec2(0.3,0.6), vec2(0.7,0.6), 
                           vec2(0.5,0.2), vec2(0.5,0.8), vec2(0.1,0.7), vec2(0.9,0.7));
    vec2 pos = vec2(x_coord, y_coord);
    float total = 0.0;
    for (int i = 0; i < 8; i++) {
        float fft = texture(fft_data, vec2(float(i) / 8.0, 0.5)).r;
        float node_size = 0.02 + fft * 0.03;
        float node_dist = distance(pos, nodes[i]);
        if (node_dist < node_size) {
            total += (1.0 - node_dist / node_size) * (0.5 + fft);
        }
        for (int j = i + 1; j < 8; j++) {
            vec2 conn = nodes[j] - nodes[i];
            float conn_len = length(conn);
            vec2 conn_dir = conn / conn_len;
            float t = dot(pos - nodes[i], conn_dir) / conn_len;
            if (t >= 0.0 && t <= 1.0) {
                vec2 closest = nodes[i] + t * conn;
                float line_dist = distance(pos, closest);
                if (line_dist < 0.005) {
                    float packet_pos = fract(time * 2.0 + float(i + j));
                    if (abs(t - packet_pos) < 0.05) {
                        total += (1.0 - abs(t - packet_pos) / 0.05) * fft * 0.8;
                    }
                    total += 0.1 * fft;
                }
            }
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
