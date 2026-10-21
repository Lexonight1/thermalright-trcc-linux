// KarmaViz waveform "mycelium_network" (organic): Fungal mycelium network growth
// From KarmaViz 67e8a15 waveforms/organic/mycelium_network.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float network = 0.0;
    
    // Mycelium nodes
    for (int i = 0; i < 12; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 12.0, 0.5)).r;
        
        float node_x = fract(sin(fi * 12.9898) * 43758.5453);
        float node_y = fract(sin(fi * 78.233) * 43758.5453);
        
        float dist = distance(vec2(x_coord, y_coord), vec2(node_x, node_y));
        
        // Node intensity
        network += exp(-dist * 15.0) * abs(sample);
        
        // Connections to nearby nodes
        for (int j = i + 1; j < 12; j++) {
            float fj = float(j);
            float other_x = fract(sin(fj * 12.9898) * 43758.5453);
            float other_y = fract(sin(fj * 78.233) * 43758.5453);
            
            float node_dist = distance(vec2(node_x, node_y), vec2(other_x, other_y));
            if (node_dist < 0.3) {
                // Distance to connection line
                vec2 line_start = vec2(node_x, node_y);
                vec2 line_end = vec2(other_x, other_y);
                vec2 point = vec2(x_coord, y_coord);
                
                float line_dist = distance(point, mix(line_start, line_end, 
                    clamp(dot(point - line_start, line_end - line_start) / 
                          dot(line_end - line_start, line_end - line_start), 0.0, 1.0)));
                
                network += exp(-line_dist * 40.0) * abs(sample) * 0.3;
            }
        }
    }
    
    return clamp(network, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
