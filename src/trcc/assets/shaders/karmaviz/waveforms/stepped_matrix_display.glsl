// KarmaViz waveform "stepped_matrix_display" (digital): Digital matrix display with stepped quantization
// From KarmaViz 67e8a15 waveforms/digital/stepped_matrix_display.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Create matrix grid
    float matrix_cols = 24.0;
    float matrix_rows = 16.0;
    
    vec2 matrix_pos = vec2(x_coord * matrix_cols, y_coord * matrix_rows);
    vec2 cell_coord = floor(matrix_pos);
    vec2 cell_fract = fract(matrix_pos);
    
    // Sample audio based on column
    float sample_x = cell_coord.x / matrix_cols;
    float sample = texture(waveform_data, vec2(sample_x, 0.5)).r;
    
    // Create stepped levels
    float stepped_sample = floor(sample * matrix_rows) / matrix_rows;
    
    // Calculate target height for this column
    float target_height = stepped_sample * matrix_rows;
    
    // Light up cells from bottom to target height
    float cell_y = matrix_rows - 1.0 - cell_coord.y; // Flip Y coordinate
    
    float intensity = 0.0;
    
    if (cell_y <= target_height) {
        // Create digital display effect within each cell
        vec2 digit_pos = cell_fract;
        
        // Create 7-segment-like display pattern
        float segment_intensity = 0.0;
        
        // Horizontal segments
        if ((digit_pos.y < 0.2 || digit_pos.y > 0.8 || abs(digit_pos.y - 0.5) < 0.1) && 
            digit_pos.x > 0.1 && digit_pos.x < 0.9) {
            segment_intensity = 1.0;
        }
        
        // Vertical segments
        if ((digit_pos.x < 0.2 || digit_pos.x > 0.8) && 
            digit_pos.y > 0.1 && digit_pos.y < 0.9) {
            segment_intensity = max(segment_intensity, 0.8);
        }
        
        // Intensity based on height position
        float height_factor = 1.0 - (cell_y / target_height);
        intensity = segment_intensity * (0.3 + height_factor * 0.7);
    }
    
    // Add grid lines
    if (cell_fract.x < 0.05 || cell_fract.y < 0.05) {
        intensity = max(intensity, 0.1);
    }
    
    return intensity * waveform_scale;
}
