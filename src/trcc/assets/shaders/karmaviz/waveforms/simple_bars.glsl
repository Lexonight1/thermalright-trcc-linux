// KarmaViz waveform "simple_bars" (Digital): Simple fragment-based bars test
// From KarmaViz 67e8a15 waveforms/Digital/simple_bars.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Simple fragment-based bars
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Create 8 simple bars
    float num_bars = 20 * waveform_scale;
    float bar_index = floor(x_coord * num_bars);
    float bar_pos = fract(x_coord * num_bars);
    
    // Leave gaps between bars
    if (bar_pos > 0.8) {
        return 0.0;
    }
    
    // Sample audio for this bar
    float sample_x = (bar_index + 0.5) / num_bars;
    float audio_level = texture(waveform_data, vec2(sample_x, 0.5)).r;
    
    // Bar height from bottom
    float bar_height = audio_level * 0.5 + 0.3;
    
    // Check if pixel is within bar
    if (y_coord < bar_height) {
        return 1.0;
    }
    
    return 0.0;
}

// Backward compatibility function
float compute_waveform_at_x(float x_coord) {
    return 0.0;
}
