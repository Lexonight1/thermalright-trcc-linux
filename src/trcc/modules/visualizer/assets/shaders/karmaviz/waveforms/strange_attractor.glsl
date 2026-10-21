// KarmaViz waveform "strange_attractor" (experimental): Strange attractor pattern driven by audio
// From KarmaViz 67e8a15 waveforms/experimental/strange_attractor.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    
    // Strange attractor simulation
    float x = x_coord - 0.5;
    float y = y_coord - 0.5;
    
    for (int i = 0; i < 16; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 16.0, 0.5)).r;
        
        // Lorenz-like equations with audio modulation
        float a = 10.0 + sample * 5.0;
        float b = 28.0 + sample * 10.0;
        float c = 8.0/3.0;
        
        float dx = a * (y - x);
        float dy = x * (b - 1.0) - y;
        
        x += dx * 0.01;
        y += dy * 0.01;
        
        float dist = distance(vec2(x_coord, y_coord), vec2(x + 0.5, y + 0.5));
        total += exp(-dist * 20.0) * abs(sample);
    }
    
    return clamp(total, 0.0, 0.9);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
