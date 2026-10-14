// KarmaViz waveform "stepped_vertical_levels" (digital): Vertical stepped levels in addition to horizontal quantization
// From KarmaViz 67e8a15 waveforms/digital/stepped_vertical_levels.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Sample audio data
    float norm_x = clamp(x_coord, 0.0, 1.0);
    float sample = texture(waveform_data, vec2(norm_x, 0.5)).r;
    
    // Create horizontal stepped levels (amplitude quantization)
    float h_steps = 16.0;
    float stepped_sample = floor(sample * h_steps) / h_steps;
    
    // Create vertical stepped levels (position quantization)
    float v_steps = 12.0;
    float stepped_y = floor(y_coord * v_steps) / v_steps;
    
    // Calculate waveform height at this x position
    float waveform_height = 0.5 + stepped_sample * waveform_scale * 0.4;
    
    // Create stepped visualization
    float intensity = 0.0;
    
    // Main waveform line (thicker, stepped)
    if (abs(stepped_y - waveform_height) < 0.04) {
        intensity = 1.0;
    }
    
    // Vertical grid lines at stepped positions
    float x_steps = 32.0;
    float stepped_x = floor(x_coord * x_steps) / x_steps;
    if (abs(x_coord - stepped_x) < 0.005) {
        intensity = max(intensity, 0.3);
    }
    
    // Horizontal grid lines at stepped positions
    if (abs(y_coord - stepped_y) < 0.002) {
        intensity = max(intensity, 0.2);
    }
    
    // Fill area below waveform with stepped pattern
    if (stepped_y < waveform_height) {
        float fill_intensity = (waveform_height - stepped_y) / waveform_height;
        intensity = max(intensity, fill_intensity * 0.4);
    }
    
    return intensity;
}
