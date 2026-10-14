// KarmaViz waveform "Fragment Grid Test" (Digital): Simple grid pattern to test fragment-based rendering
// From KarmaViz 67e8a15 waveforms/digital/fragment_grid_test.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Simple fragment test - draws a grid pattern
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Create a simple grid pattern to test fragment-based rendering
    float grid_size = 10.0;
    float x_grid = fract(x_coord * grid_size);
    float y_grid = fract(y_coord * grid_size);
    
    // Draw grid lines
    float line_width = 0.1;
    if (x_grid < line_width || y_grid < line_width) {
        return 1.0;
    }
    
    return 0.0;
}

// Keep the old function for backward compatibility
float compute_waveform_at_x(float x_coord) {
    return 0.0;
}
