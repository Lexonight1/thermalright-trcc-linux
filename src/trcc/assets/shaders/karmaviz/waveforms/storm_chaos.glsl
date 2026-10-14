// KarmaViz waveform "Storm Chaos" (natural): Chaotic multi-directional lightning storm
// From KarmaViz 67e8a15 waveforms/natural/storm_chaos.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float total = 0.0;
    float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;
    
    // Multiple lightning bolts from different directions
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float bolt_seed = fi * 23.7 + time * 0.3;
        float bolt_trigger = sin(time * 12.0 + fi * 1.8) * 0.5 + 0.5;
        
        if (bolt_trigger > 0.5 && fft_high > 0.2) {
            // Vary bolt direction and origin
            float bolt_angle = fi * 1.047; // ~60 degrees apart
            float bolt_origin_x = 0.5 + sin(bolt_angle) * 0.4;
            float bolt_origin_y = 0.5 + cos(bolt_angle) * 0.4;
            
            // Create bolt path from origin towards center with chaos
            vec2 to_center = vec2(0.5, 0.5) - vec2(bolt_origin_x, bolt_origin_y);
            float path_progress = dot(vec2(x_coord, y_coord) - vec2(bolt_origin_x, bolt_origin_y), to_center) / dot(to_center, to_center);
            path_progress = clamp(path_progress, 0.0, 1.0);
            
            // Calculate ideal position on straight line
            vec2 ideal_pos = vec2(bolt_origin_x, bolt_origin_y) + to_center * path_progress;
            
            // Add chaotic deviation
            float chaos = sin(path_progress * 20.0 + bolt_seed) * 0.1 * path_progress +
                         sin(path_progress * 45.0 + bolt_seed * 1.3) * 0.05 * path_progress +
                         sin(path_progress * 80.0 + bolt_seed * 2.1) * 0.025 * path_progress;
            
            // Perpendicular deviation
            vec2 perp = vec2(-to_center.y, to_center.x);
            perp = normalize(perp) * chaos;
            vec2 bolt_pos = ideal_pos + perp;
            
            float bolt_dist = distance(vec2(x_coord, y_coord), bolt_pos);
            
            // Only render if we're close to the bolt path
            if (path_progress > 0.0 && path_progress < 1.0) {
                // Bolt core
                if (bolt_dist < 0.004) {
                    float core_intensity = (1.0 - bolt_dist / 0.004) * 12.0;
                    total += core_intensity;
                }
                
                // Bolt glow
                if (bolt_dist < 0.015) {
                    float glow_intensity = (1.0 - bolt_dist / 0.015) * 4.0;
                    total += glow_intensity;
                }
            }
        }
    }
    
    // Ground strikes - vertical bolts that hit the bottom
    for (int g = 0; g < 3; g++) {
        float fg = float(g);
        float strike_trigger = sin(time * 15.0 + fg * 2.5) * 0.5 + 0.5;
        
        if (strike_trigger > 0.4 && fft_low > 0.2) {
            float strike_x = 0.2 + fg * 0.3 + sin(time * 4.0 + fg) * 0.1;
            
            // Vertical bolt with increasing chaos towards bottom
            float strike_path = strike_x + sin(y_coord * 25.0 + time * 20.0 + fg * 3.0) * 0.06 * y_coord;
            float strike_dist = abs(x_coord - strike_path);
            
            // Only render in bottom half of screen
            if (y_coord < 0.6) {
                if (strike_dist < 0.003) {
                    float strike_intensity = (1.0 - strike_dist / 0.003) * 10.0;
                    total += strike_intensity;
                }
                
                if (strike_dist < 0.012) {
                    total += (1.0 - strike_dist / 0.012) * 3.0;
                }
                
                // Ground impact glow
                if (y_coord < 0.1) {
                    float impact_dist = distance(vec2(x_coord, y_coord), vec2(strike_x, 0.0));
                    if (impact_dist < 0.08) {
                        total += (1.0 - impact_dist / 0.08) * 5.0 * (1.0 - y_coord / 0.1);
                    }
                }
            }
        }
    }
    
    // Atmospheric electrical activity - random sparks
    for (int a = 0; a < 15; a++) {
        float fa = float(a);
        float spark_seed = fa * 15.7 + time * 2.0;
        float spark_x = fract(sin(spark_seed) * 43758.5453);
        float spark_y = fract(sin(spark_seed * 1.3) * 43758.5453);
        float spark_trigger = fract(sin(spark_seed * 2.1) * 43758.5453);
        
        if (spark_trigger > 0.8) {
            float spark_dist = distance(vec2(x_coord, y_coord), vec2(spark_x, spark_y));
            if (spark_dist < 0.008) {
                total += (1.0 - spark_dist / 0.008) * 3.0;
            }
        }
    }
    
    // Storm intensity flash - entire screen flickers during intense moments
    if (fft_high > 0.5 && fft_low > 0.4) {
        float storm_flash = sin(time * 50.0) * 0.5 + 0.5;
        total += storm_flash * 2.0 * (fft_high + fft_low) * 0.5;
    }
    
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
