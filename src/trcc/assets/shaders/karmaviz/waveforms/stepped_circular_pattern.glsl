// KarmaViz waveform "stepped_circular_pattern" (digital): Stepped waves following circular geometric patterns
// From KarmaViz 67e8a15 waveforms/digital/stepped_circular_pattern.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Convert to polar coordinates
    vec2 center = vec2(0.5, 0.5);
    vec2 pos = vec2(x_coord, y_coord) - center;
    float angle = atan(pos.y, pos.x);
    float radius = length(pos);
    
    // Normalize angle to 0-1 range
    float norm_angle = (angle + 3.14159) / (2.0 * 3.14159);
    
    // Sample audio based on angle
    float sample = texture(waveform_data, vec2(norm_angle, 0.5)).r;
    
    // Create stepped levels
    float stepped_sample = floor(sample * 10.0) / 10.0;
    
    // Create circular patterns based on stepped sample
    float target_radius = stepped_sample * 0.4; // Max radius of 0.4
    
    // Create multiple concentric circles
    float circle_width = 0.03;
    float num_circles = floor(target_radius / circle_width);
    
    float intensity = 0.0;
    for (float i = 1.0; i <= num_circles; i += 1.0) {
        float circle_radius = i * circle_width;
        float dist_to_circle = abs(radius - circle_radius);
        
        if (dist_to_circle < circle_width * 0.3) {
            intensity = max(intensity, (1.0 - i / num_circles) * 0.8 + 0.2);
        }
    }
    
    return intensity * waveform_scale;
}
