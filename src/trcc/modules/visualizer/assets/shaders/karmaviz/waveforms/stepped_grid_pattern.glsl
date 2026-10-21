// KarmaViz waveform "stepped_grid_pattern" (digital): Stepped waves following grid geometric patterns
// From KarmaViz 67e8a15 waveforms/digital/stepped_grid_pattern.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Create grid pattern
    float grid_size = 8.0;
    vec2 grid_pos = vec2(x_coord, y_coord) * grid_size;
    vec2 grid_cell = floor(grid_pos);
    vec2 cell_pos = fract(grid_pos);
    
    // Sample audio based on grid cell position
    float sample_coord = fract((grid_cell.x + grid_cell.y * 0.1) * 0.1);
    float sample = texture(waveform_data, vec2(sample_coord, 0.5)).r;
    
    // Create stepped levels
    float stepped_sample = floor(sample * 12.0) / 12.0;
    
    // Create different patterns within each cell based on stepped sample
    float intensity = 0.0;
    
    if (stepped_sample > 0.75) {
        // Full cell
        intensity = 1.0;
    } else if (stepped_sample > 0.5) {
        // Cross pattern
        if (abs(cell_pos.x - 0.5) < 0.2 || abs(cell_pos.y - 0.5) < 0.2) {
            intensity = 0.8;
        }
    } else if (stepped_sample > 0.25) {
        // Corner dots
        vec2 corner_dist = min(cell_pos, 1.0 - cell_pos);
        if (min(corner_dist.x, corner_dist.y) < 0.3) {
            intensity = 0.6;
        }
    } else if (stepped_sample > 0.1) {
        // Center dot
        if (distance(cell_pos, vec2(0.5)) < 0.3) {
            intensity = 0.4;
        }
    }
    
    return intensity * waveform_scale;
}
