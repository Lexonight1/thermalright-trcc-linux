// KarmaViz waveform "lissajous_curves" (mathematical): Lissajous curve patterns
// From KarmaViz 67e8a15 waveforms/mathematical/lissajous_curves.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    
    // Multiple Lissajous curves
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 6.0, 0.5)).r;
        
        float freq_x = fi + 1.0;
        float freq_y = fi * 0.7 + 1.0;
        float phase = fi * 0.5;
        
        float liss_x = 0.5 + sin(time * freq_x + phase) * 0.3;
        float liss_y = 0.5 + cos(time * freq_y) * 0.3;
        
        float dist = distance(vec2(x_coord, y_coord), vec2(liss_x, liss_y));
        total += exp(-dist * 20.0) * abs(sample);
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
