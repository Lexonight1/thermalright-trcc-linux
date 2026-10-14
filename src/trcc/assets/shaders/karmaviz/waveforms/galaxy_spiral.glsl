// KarmaViz waveform "galaxy_spiral" (cosmic): Spiral galaxy arm structure
// From KarmaViz 67e8a15 waveforms/cosmic/galaxy_spiral.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Galaxy center
    float center_x = 0.33;
    float center_y = 0.667;
    
    // Convert to polar coordinates
    float dx = x_coord - center_x;
    float dy = y_coord - center_y;
    float radius = waveform_scale * length(vec2(dx, dy));
    float angle = atan(dy, dx);
    
    // Spiral arms
    float spiral_tightness = 3.0;
    float spiral_angle = angle + spiral_tightness * log(radius + 0.1);
    
    // Multiple spiral arms
    float arms = 0.0;
    for (int i = 0; i < 4; i++) {
        float arm_offset = float(i) * 1.5708; // 90 degrees apart
        float arm_spiral = spiral_angle + arm_offset + time * 0.1;
        
        // Arm intensity
        float arm_intensity = exp(-pow(sin(arm_spiral * 2.0), 2.0) * 20.0) * glow_radius;
        
        // Audio modulation
        float sample = texture(waveform_data, vec2(radius, 0.5)).r;
        arms += arm_intensity * abs(sample);
    }
    
    // Galaxy bulge
    float bulge = exp(-radius * 8.0) * 0.5;
    
    // Star formation regions
    float star_formation = sin(radius * 20.0 + time * 2.0) * 0.1 + 0.9;
    
    return clamp((arms + bulge) * star_formation, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
