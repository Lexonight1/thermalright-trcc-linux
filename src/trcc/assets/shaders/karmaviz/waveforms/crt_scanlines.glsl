// KarmaViz waveform "crt_scanlines" (retro): CRT monitor scanline effect
// From KarmaViz 67e8a15 waveforms/retro/crt_scanlines.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Sample audio
    float sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    
    // CRT scanlines
    float scanline = sin(y_coord * 200.0) * 0.1 + 0.9;
    
    // Waveform display area
    float wave_center = 0.5 + sample * 0.3;
    float wave_thickness = 0.02 + abs(sample) * 0.01;
    float wave_intensity = 1.0 - smoothstep(0.0, wave_thickness, abs(y_coord - wave_center));
    
    return wave_intensity * scanline;
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
