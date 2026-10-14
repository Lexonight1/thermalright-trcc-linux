// KarmaViz waveform "coral_reef" (organic): Organic coral reef growth pattern
// From KarmaViz 67e8a15 waveforms/organic/coral_reef.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float coral = 0.0;
    
    // Multiple coral branches
    for (int i = 0; i < 8; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 8.0, 0.5)).r;
        
        // Branch center
        float branch_x = 0.2 + 0.6 * sin(fi * 2.1);
        float branch_y = 0.1 + 0.8 * (fi / 8.0);
        
        // Branch growth
        float dist = distance(vec2(x_coord, y_coord), vec2(branch_x, branch_y));
        float growth = exp(-dist * 8.0) * abs(sample);
        
        // Organic texture
        float texture_noise = sin(x_coord * 20.0 + fi) * cos(y_coord * 15.0 + fi) * 0.1;
        growth *= (1.0 + texture_noise);
        
        coral += growth;
    }
    
    return clamp(coral, 0.0, 0.6);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
