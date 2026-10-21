// KarmaViz waveform "laser_grid" (lighting): Geometric laser grid patterns with rotating grids, intersection highlights, and audio-reactive spacing
// From KarmaViz 67e8a15 waveforms/lighting/laser_grid.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Laser Grid Waveform - Geometric laser grid patterns

float grid_line_intensity(vec2 pos, float spacing, float line_width, float brightness) {
    vec2 grid_pos = pos / spacing;
    vec2 grid_fract = fract(grid_pos);
    
    // Distance to nearest grid line (horizontal or vertical)
    float dist_h = min(grid_fract.y, 1.0 - grid_fract.y) * spacing;
    float dist_v = min(grid_fract.x, 1.0 - grid_fract.x) * spacing;
    float dist = min(dist_h, dist_v);
    
    if (dist > line_width) {
        return 0.0;
    }
    
    float intensity = 1.0 - (dist / line_width);
    intensity = pow(intensity, 2.0); // Sharp laser lines
    
    return intensity * brightness;
}

float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    vec2 pos = vec2(x_coord, y_coord);
    
    // Sample audio data
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Audio-reactive parameters
    float audio_intensity = (abs(audio_sample) + fft_sample) * 0.5;
    float time_factor = time * 0.6;
    
    float total_intensity = 0.0;
    
    // Main grid pattern
    float spacing1 = 0.1 + sin(time_factor * 0.3) * 0.03;
    float line_width1 = 0.002 + audio_intensity * 0.001;
    float brightness1 = 0.6 + audio_intensity * 0.4;
    
    float grid1 = grid_line_intensity(pos, spacing1, line_width1, brightness1);
    total_intensity += grid1;
    
    // Secondary finer grid
    float spacing2 = 0.05 + cos(time_factor * 0.4) * 0.02;
    float line_width2 = 0.001 + fft_sample * 0.0005;
    float brightness2 = 0.3 + fft_sample * 0.3;
    
    float grid2 = grid_line_intensity(pos, spacing2, line_width2, brightness2);
    total_intensity += grid2 * 0.5;
    
    // Animated diagonal grid
    vec2 rotated_pos = pos;
    float angle = time_factor * 0.2;
    float cos_a = cos(angle);
    float sin_a = sin(angle);
    rotated_pos = vec2(
        cos_a * (pos.x - 0.5) - sin_a * (pos.y - 0.5) + 0.5,
        sin_a * (pos.x - 0.5) + cos_a * (pos.y - 0.5) + 0.5
    );
    
    float spacing3 = 0.08 + sin(time_factor * 0.5) * 0.02;
    float line_width3 = 0.0015 + audio_intensity * 0.0008;
    float brightness3 = 0.4 + audio_intensity * 0.2;
    
    float grid3 = grid_line_intensity(rotated_pos, spacing3, line_width3, brightness3);
    total_intensity += grid3 * 0.7;
    
    // Audio-reactive intersection highlights
    if (audio_intensity > 0.3) {
        // Highlight grid intersections
        vec2 grid_pos = pos / spacing1;
        vec2 grid_fract = fract(grid_pos);
        
        float intersection_dist = distance(grid_fract, vec2(0.0)) + distance(grid_fract, vec2(1.0, 0.0)) + 
                                 distance(grid_fract, vec2(0.0, 1.0)) + distance(grid_fract, vec2(1.0));
        intersection_dist = min(intersection_dist, 1.0);
        
        if (intersection_dist < 0.1) {
            total_intensity += (1.0 - intersection_dist / 0.1) * audio_intensity * 0.5;
        }
    }
    
    // Pulsing effect
    float pulse = sin(time * 3.0 + audio_intensity * 8.0) * 0.1 + 0.9;
    total_intensity *= pulse;
    
    return clamp(total_intensity, 0.0, 1.0);
}

// 1D compatibility function
float compute_waveform_at_x(float x_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Simple 1D grid lines
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    float spacing = 0.1 + sin(time * 0.5) * 0.03;
    float grid_pos = x_coord / spacing;
    float grid_fract = fract(grid_pos);
    
    float dist = min(grid_fract, 1.0 - grid_fract) * spacing;
    float line_width = 0.01 + fft_sample * 0.005;
    
    float intensity = smoothstep(line_width, 0.0, dist);
    intensity *= (0.6 + audio_sample * 0.4);
    
    return clamp(intensity, 0.0, 1.0);
}
