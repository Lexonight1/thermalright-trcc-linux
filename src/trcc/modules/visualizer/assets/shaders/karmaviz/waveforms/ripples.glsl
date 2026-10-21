// KarmaViz waveform "ripples" (basic): Concentric ripples emanating from center
// From KarmaViz 67e8a15 waveforms/basic/ripples.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float center_x = 0.5;
    float center_y = 0.5;
    float dist = distance(vec2(x_coord, y_coord), vec2(center_x, center_y));
    
    // Sample audio at distance-based position
    float audio_pos = clamp(dist * 2.0, 0.0, 1.0);
    float sample = texture(waveform_data, vec2(audio_pos, 0.5)).r;
    
    // Create ripple effect
    float ripple = sin(dist * 20.0 - time * 3.0) * 0.5 + 0.5;
    return sample * ripple * (1.0 - dist);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
