// KarmaViz waveform "vector_scope" (retro): Retro vector scope display
// From KarmaViz 67e8a15 waveforms/retro/vector_scope.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    
    // Vector scope traces
    for (int i = 0; i < 16; i++) {
        float fi = float(i);
        float t = fi / 16.0;
        
        float sample1 = texture(waveform_data, vec2(t, 0.5)).r;
        float sample2 = texture(waveform_data, vec2(fract(t + 0.1), 0.5)).r;
        
        float trace_x = 0.5 + sample1 * 0.4;
        float trace_y = 0.5 + sample2 * 0.4;
        
        float dist = distance(vec2(x_coord, y_coord), vec2(trace_x, trace_y));
        total += exp(-dist * 100.0) * (1.0 - t * 0.5);
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
