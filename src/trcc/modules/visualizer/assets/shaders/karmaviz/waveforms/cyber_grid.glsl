// KarmaViz waveform "cyber_grid" (Digital): Cyberpunk-style grid with flowing data connections
// From KarmaViz 67e8a15 waveforms/digital/cyber_grid.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Cyber Grid
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    float grid_size = 16.0;
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    
    // Grid coordinates
    float grid_x = floor(x_coord * grid_size) / grid_size;
    float grid_y = floor(y_coord * grid_size) / grid_size;
    
    // Grid lines
    float line_width = 0.003 + fft * 0.005;
    if (abs(fract(x_coord * grid_size) - 0.5) < line_width * grid_size ||
        abs(fract(y_coord * grid_size) - 0.5) < line_width * grid_size) {
        
        float line_activity = sin(time * 3.0 + grid_x * 10.0 + grid_y * 8.0) * 0.5 + 0.5;
        total += line_activity * (0.4 + fft * 0.6);
    }
    
    // Grid intersections (nodes)
    float node_x = grid_x + 0.5 / grid_size;
    float node_y = grid_y + 0.5 / grid_size;
    float node_dist = distance(vec2(x_coord, y_coord), vec2(node_x, node_y));
    float node_size = 0.01 + fft * 0.02;
    
    if (node_dist < node_size) {
        float node_activity = sin(time * 5.0 + grid_x * 12.0 + grid_y * 9.0) * 0.5 + 0.5;
        total += (1.0 - node_dist / node_size) * node_activity * (0.6 + fft * 0.4);
    }
    
    // Data flow along grid
    float flow_time = time * 2.0;
    float flow_x = fract(flow_time + grid_y * 3.0);
    float flow_y = fract(flow_time * 0.8 + grid_x * 2.5);
    
    if (abs(x_coord - (grid_x + flow_x / grid_size)) < 0.005) {
        total += 0.5 * fft;
    }
    if (abs(y_coord - (grid_y + flow_y / grid_size)) < 0.005) {
        total += 0.5 * fft;
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
