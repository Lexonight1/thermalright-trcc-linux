// KarmaViz waveform "laser_scanner" (lighting): Security-style laser scanners with horizontal and vertical sweeps, burst modes, and blinking effects
// From KarmaViz 67e8a15 waveforms/lighting/laser_scanner.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Laser Scanner Waveform - Scanning laser patterns like security systems

float scanner_beam_intensity(vec2 pos, float scan_pos, float scan_dir, float width, float brightness) {
    float dist;
    
    if (scan_dir < 0.5) {
        // Horizontal scan
        dist = abs(pos.y - scan_pos);
    } else {
        // Vertical scan
        dist = abs(pos.x - scan_pos);
    }
    
    if (dist > width) {
        return 0.0;
    }
    
    float intensity = 1.0 - (dist / width);
    intensity = pow(intensity + glow_radius * 4, 3.0); // Sharp laser beam
    
    return intensity * brightness;
}

float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    vec2 pos = vec2(x_coord, y_coord);
    
    // Sample audio data
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Audio-reactive parameters
    float audio_intensity = (abs(audio_sample) + fft_sample) * 0.5;
    float time_factor = time * 1.5;
    
    float total_intensity = 0.0;
    
    // Scanner 1: Horizontal sweep
    float scan_pos1 = sin(time_factor * 0.8) * 0.4 + 0.5;
    float width1 = 0.008 + audio_intensity * 0.004;
    float brightness1 = 0.8 + audio_intensity * 0.4;
    
    float scanner1 = scanner_beam_intensity(pos, scan_pos1, 0.0, width1, brightness1);
    total_intensity += scanner1;
    
    // Scanner 2: Vertical sweep (different speed)
    float scan_pos2 = cos(time_factor * 1.2) * 0.35 + 0.5;
    float width2 = 0.006 + fft_sample * 0.003;
    float brightness2 = 0.7 + fft_sample * 0.5;
    
    float scanner2 = scanner_beam_intensity(pos, scan_pos2, 1.0, width2, brightness2);
    total_intensity += scanner2 * 0.8;
    
    // Scanner 3: Fast horizontal scan
    float scan_pos3 = sin(time_factor * 2.5) * 0.3 + 0.5;
    float width3 = 0.004 + audio_intensity * 0.002;
    float brightness3 = 0.6 + audio_intensity * 0.3;
    
    float scanner3 = scanner_beam_intensity(pos, scan_pos3, 0.0, width3, brightness3);
    total_intensity += scanner3 * 0.6;
    
    // Scanner 4: Audio-reactive burst scanning
    if (audio_intensity > 0.4) {
        float burst_time = time_factor * 8.0;
        float scan_pos4 = fract(burst_time) * 0.8 + 0.1;
        float width4 = 0.003 + audio_intensity * 0.001;
        float brightness4 = audio_intensity * 1.2;
        
        // Alternate between horizontal and vertical
        float scan_dir4 = step(0.5, fract(burst_time * 0.5));
        
        float scanner4 = scanner_beam_intensity(pos, scan_pos4, scan_dir4, width4, brightness4);
        total_intensity += scanner4;
    }
    
    // Add trailing effect
    if (total_intensity > 0.1) {
        // Create a subtle trail behind the main beam
        float trail_factor = sin(time * 10.0) * 0.1 + 0.9;
        total_intensity *= trail_factor;
        
        // Add some sparkle at the beam edges
        if (total_intensity > 0.5 && total_intensity < 0.8) {
            total_intensity += sin(time * 20.0 + pos.x * 100.0 + pos.y * 100.0) * 0.1;
        }
    }
    
    // Security system style blinking
    float blink = step(0.8, sin(time * 4.0));
    total_intensity *= (0.3 + blink * 0.7);
    
    return clamp(total_intensity, 0.0, 1.0);
}

// 1D compatibility function
float compute_waveform_at_x(float x_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Simple 1D scanning effect
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Scanning position
    float scan_pos = sin(time * 2.0) * 0.4 + 0.5;
    float dist = abs(x_coord - scan_pos);
    
    float width = 0.03 + fft_sample * 0.02;
    float intensity = smoothstep(width, 0.0, dist);
    intensity = pow(intensity, 2.0);
    
    intensity *= (0.7 + audio_sample * 0.5);
    
    // Blinking effect
    float blink = step(0.7, sin(time * 3.0));
    intensity *= (0.4 + blink * 0.6);
    
    return clamp(intensity, 0.0, 1.0);
}
