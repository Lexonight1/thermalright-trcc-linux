// KarmaViz waveform "sierpinski_audio" (fractal): Sierpinski triangle with audio modulation
// From KarmaViz 67e8a15 waveforms/fractal/sierpinski_audio.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Sierpinski triangle generation
    float x = x_coord;
    float y = y_coord;
    
    float intensity = 0.0;
    float scale = 1.0;
    
    for (int i = 0; i < 8; i++) {
        float sample = texture(waveform_data, vec2(float(i) / 8.0, 0.5)).r;
        
        x *= 2.0;
        y *= 2.0;
        
        if (x > 1.0) x -= 1.0;
        if (y > 1.0) y -= 1.0;
        
        // Sierpinski condition
        if (x + y < 1.0) {
            intensity += abs(sample) * scale;
        }
        
        scale *= 0.5;
    }
    
    return clamp(intensity, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
