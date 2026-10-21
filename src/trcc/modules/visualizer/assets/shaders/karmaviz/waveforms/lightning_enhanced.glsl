// KarmaViz waveform "lightning_enhanced" (natural): Enhanced lightning with realistic branching and thunder
// From KarmaViz 67e8a15 waveforms/natural/lightning_enhanced.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;
    
    // Enhanced lightning bolts with realistic branching
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        float bolt_seed = fi * 17.3 + time * 0.5;
        float bolt_trigger = sin(time * 8.0 + fi * 2.7) * 0.5 + 0.5;
        
        // Much more frequent lightning triggers
        if (bolt_trigger > 0.3 && fft_high > 0.1) {
            // Main bolt path - starts from top and zigzags down
            float bolt_start_x = 0.15 + fi * 0.2 + sin(time * 3.0 + fi) * 0.1;
            
            // Create zigzag pattern that gets more chaotic as it goes down
            float chaos_factor = y_coord * y_coord; // More chaos at bottom
            float main_path = bolt_start_x + 
                            sin(y_coord * 15.0 + bolt_seed) * 0.08 * chaos_factor +
                            sin(y_coord * 35.0 + bolt_seed * 1.7) * 0.03 * chaos_factor +
                            sin(y_coord * 60.0 + bolt_seed * 2.3) * 0.015 * chaos_factor;
            
            float main_bolt_dist = abs(x_coord - main_path);
            
            // Main bolt core - extremely bright
            if (main_bolt_dist < 0.003) {
                float core_intensity = (1.0 - main_bolt_dist / 0.003) * 15.0;
                total += core_intensity;
            }
            
            // Main bolt glow - much brighter
            if (main_bolt_dist < 0.02) {
                float glow_intensity = (1.0 - main_bolt_dist / 0.02) * 5.0;
                total += glow_intensity;
            }
            
            // Lightning branches - smaller bolts that branch off
            for (int b = 0; b < 3; b++) {
                float fb = float(b);
                float branch_y_start = 0.3 + fb * 0.2; // Where branch starts
                
                if (y_coord > branch_y_start && y_coord < branch_y_start + 0.3) {
                    float branch_progress = (y_coord - branch_y_start) / 0.3;
                    float branch_angle = (fb - 1.0) * 0.5; // -0.5, 0, 0.5 for left, center, right
                    
                    float branch_x = main_path + branch_angle * branch_progress * 0.3;
                    branch_x += sin(y_coord * 25.0 + bolt_seed + fb * 3.0) * 0.04 * branch_progress;
                    
                    float branch_dist = abs(x_coord - branch_x);
                    
                    // Branch intensity decreases with distance from main bolt
                    float branch_strength = 0.7 * (1.0 - branch_progress * 0.5);
                    
                    if (branch_dist < 0.002) {
                        total += (1.0 - branch_dist / 0.002) * branch_strength * 8.0;
                    }
                    
                    // Branch glow
                    if (branch_dist < 0.01) {
                        total += (1.0 - branch_dist / 0.01) * 2.0 * branch_strength;
                    }
                }
            }
            
            // Electric field effect around lightning
            if (main_bolt_dist < 0.1) {
                float field_noise = sin(x_coord * 50.0 + y_coord * 30.0 + time * 20.0 + bolt_seed) * 0.5 + 0.5;
                if (field_noise > 0.7) {
                    total += (1.0 - main_bolt_dist / 0.1) * 2.0;
                }
            }
        }
    }
    
    // Enhanced rain with varying sizes and speeds
    for (int r = 0; r < 25; r++) {
        float fr = float(r);
        float drop_seed = fr * 12.9898;
        float drop_x = fract(sin(drop_seed) * 43758.5453);
        
        // Vary drop size and speed based on audio
        float drop_size = 0.003 + fft_mid * 0.004;
        float drop_speed = 1.5 + fft_mid * 2.5 + fft_low * 1.0;
        
        // Rain falls from top to bottom
        float drop_y = 1.0 - fract(time * drop_speed + fr * 0.3);
        
        // Add slight horizontal drift
        float drift = sin(time * 2.0 + fr) * 0.02;
        drop_x += drift;
        
        float drop_dist = distance(vec2(x_coord, y_coord), vec2(drop_x, drop_y));
        
        if (drop_dist < drop_size) {
            float drop_intensity = (1.0 - drop_dist / drop_size) * 3.0;
            total += drop_intensity;
        }
        
        // Rain splash effect when drops hit bottom
        if (drop_y < 0.05) {
            float splash_radius = 0.02 + fft_mid * 0.01;
            float splash_dist = distance(vec2(x_coord, y_coord), vec2(drop_x, 0.0));
            if (splash_dist < splash_radius) {
                total += (1.0 - splash_dist / splash_radius) * 2.0 * (1.0 - drop_y / 0.05);
            }
        }
    }
    
    // Thunder rumble effect - screen-wide flash during high bass
    if (fft_low > 0.4) {
        float thunder_flash = sin(time * 30.0) * 0.5 + 0.5;
        total += thunder_flash * 3.0 * fft_low;
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
