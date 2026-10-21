// KarmaViz waveform "led_matrix" (digital): LED matrix display pattern
// From KarmaViz 67e8a15 waveforms/digital/led_matrix.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // LED grid
    float led_x = floor(x_coord * 16.0);
    float led_y = floor(y_coord * 12.0);
    
    // LED spacing
    float grid_x = fract(x_coord * 16.0);
    float grid_y = fract(y_coord * 12.0);
    
    // LED shape (circular)
    float led_dist = distance(vec2(grid_x, grid_y), vec2(0.5, 0.5));
    float led_on = step(led_dist, 0.4);
    
    // Audio data
    float sample = texture(waveform_data, vec2(led_x / 16.0, 0.5)).r;
    
    // LED brightness
    float brightness = abs(sample) * led_on;
    
    return brightness;
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
