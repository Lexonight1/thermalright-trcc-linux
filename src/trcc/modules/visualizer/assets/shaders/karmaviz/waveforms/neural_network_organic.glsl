// KarmaViz waveform "neural_network" (organic): Neural network-like connections
// From KarmaViz 67e8a15 waveforms/organic/neural_network.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    
    // Neural nodes
    for (int i = 0; i < 10; i++) {
        float fi = float(i);
        float nx = fract(sin(fi * 12.9898) * 43758.5453);
        float ny = fract(sin(fi * 78.233) * 43758.5453);
        
        float dist = distance(vec2(x_coord, y_coord), vec2(nx, ny));
        float sample = texture(waveform_data, vec2(fi / 10.0, 0.5)).r;
        
        // Neural activation
        float activation = exp(-dist * 15.0) * abs(sample);
        total += activation;
        
        // Connections between nodes
        if (i > 0) {
            float prev_x = fract(sin((fi-1.0) * 12.9898) * 43758.5453);
            float prev_y = fract(sin((fi-1.0) * 78.233) * 43758.5453);
            
            // Distance to line between nodes
            vec2 line_start = vec2(prev_x, prev_y);
            vec2 line_end = vec2(nx, ny);
            vec2 point = vec2(x_coord, y_coord);
            
            float line_dist = distance(point, mix(line_start, line_end, 
                clamp(dot(point - line_start, line_end - line_start) / 
                      dot(line_end - line_start, line_end - line_start), 0.0, 1.0)));
            
            total += exp(-line_dist * 50.0) * abs(sample) * 0.3;
        }
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
