// KarmaViz waveform "cellular_growth" (organic): Organic cellular growth pattern
// From KarmaViz 67e8a15 waveforms/organic/cellular_growth.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    
    // Multiple growth centers
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float cx = 0.2 + 0.6 * abs(sin(fi * 2.1 + time * 0.3)) * waveform_scale * 0.5;
        float cy = 0.2 + 0.6 * abs(cos(fi * 1.7 + time * 0.2)) * waveform_scale * 0.5;
        
        float dist = distance(vec2(x_coord, y_coord), vec2(cx, cy));
        float sample = texture(waveform_data, vec2(fi / 6.0, 0.5)).r;
        
        // Organic growth function
        float growth = exp(-dist * 8.0) * (0.5 + abs(sample) * 0.5);
        total += growth + glow_radius * 0.3;
    }
    
    return clamp(total * 0.3, 0.0, 0.6);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
