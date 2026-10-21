// KarmaViz waveform "electric_bolt" (natural): Pure electric lightning bolt effect
// From KarmaViz 67e8a15 waveforms/natural/electric_bolt.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    
    // Sample waveform data for bolt path modulation
    float waveform_sample = texture(waveform_data, vec2(y_coord, 0.5)).r;
    
    // Main lightning bolt - vertical with horizontal deviation
    float bolt_center = 0.5 + waveform_sample * 0.3; // Use audio to modulate position
    
    // Create jagged lightning path using multiple sine waves
    float bolt_path = bolt_center + 
                     sin(y_coord * 12.0 + time * 8.0) * 0.15 * fft_high +
                     sin(y_coord * 28.0 + time * 15.0) * 0.08 * fft_high +
                     sin(y_coord * 45.0 + time * 25.0) * 0.04 * fft_high +
                     sin(y_coord * 80.0 + time * 40.0) * 0.02 * fft_high;
    
    float bolt_dist = abs(x_coord - bolt_path);
    
    // Lightning core - extremely bright and thin
    if (bolt_dist < 0.002) {
        float core_intensity = (1.0 - bolt_dist / 0.002) * 20.0 * glow_radius;
        total += core_intensity;
    }
    
    // Inner glow - bright white/blue
    if (bolt_dist < 0.008) {
        float inner_glow = (1.0 - bolt_dist / 0.008) * 8.0;
        total += inner_glow;
    }
    
    // Outer glow - dimmer, wider
    if (bolt_dist < 0.025) {
        float outer_glow = (1.0 - bolt_dist / 0.025) * 3.0;
        total += outer_glow;
    }
    
    // Electric discharge sparks around the main bolt
    for (int s = 0; s < 8; s++) {
        float fs = float(s);
        float spark_y = fs * 0.125; // Distribute sparks vertically
        float spark_trigger = sin(time * 20.0 + fs * 3.14) * 0.5 + 0.5;
        
        if (spark_trigger > 0.3) {
            float spark_x = bolt_path + sin(time * 30.0 + fs * 2.0) * 0.1;
            float spark_dist = distance(vec2(x_coord, y_coord), vec2(spark_x, spark_y));
            
            if (spark_dist < 0.01) {
                float spark_intensity = (1.0 - spark_dist / 0.01) * 5.0;
                total += spark_intensity;
            }
        }
    }
    
    // Electrical field distortion - creates crackling effect
    float field_noise = sin(x_coord * 60.0 + y_coord * 40.0 + time * 35.0) * 0.5 + 0.5;
    if (field_noise > 0.7 && bolt_dist < 0.05) {
        total += (1.0 - bolt_dist / 0.05) * 3.0;
    }
    
    // Plasma afterglow - fades in and out
    float afterglow_pulse = sin(time * 6.0) * 0.5 + 0.5;
    if (bolt_dist < 0.04 && afterglow_pulse > 0.3) {
        total += (1.0 - bolt_dist / 0.04) * 2.0 * afterglow_pulse;
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
