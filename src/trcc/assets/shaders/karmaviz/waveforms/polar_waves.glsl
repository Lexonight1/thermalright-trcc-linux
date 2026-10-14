// KarmaViz waveform "polar_waves" (mathematical): Polar coordinate wave patterns
// From KarmaViz 67e8a15 waveforms/mathematical/polar_waves.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Convert to polar coordinates
    float dx = x_coord - 0.5;
    float dy = y_coord - 0.5;
    float radius = length(vec2(dx, dy));
    float angle = atan(dy, dx) / 6.28318 + 0.5;
    
    // Sample audio based on angle
    float sample = texture(waveform_data, vec2(angle, 0.5)).r;
    
    // Create polar wave pattern
    float wave = sin(radius * 15.0 + time * 2.0) * sample;
    return clamp(wave * 0.5 + 0.5, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
