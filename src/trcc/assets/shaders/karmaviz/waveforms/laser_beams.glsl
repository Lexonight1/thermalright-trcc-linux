// KarmaViz waveform "laser_beams" (lighting): Sharp focused laser beams with scanning, rotating, and cross patterns that react to audio
// From KarmaViz 67e8a15 waveforms/lighting/laser_beams.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Laser Beams Waveform - Sharp focused laser beams with audio reactivity

float laser_beam_intensity(vec2 pos, vec2 start, vec2 end, float width, float brightness) {
    // Calculate distance from point to line segment
    vec2 line_vec = end - start;
    vec2 point_vec = pos - start;
    
    float line_length = length(line_vec);
    if (line_length < 0.001) return 0.0;
    
    vec2 line_dir = line_vec / line_length;
    float projection = dot(point_vec, line_dir);
    
    // Clamp projection to line segment
    projection = clamp(projection, 0.0, line_length * waveform_scale);
    
    vec2 closest_point = start + line_dir * projection;
    float dist_to_line = distance(pos, closest_point);
    
    // Sharp laser falloff
    if (dist_to_line > width) {
        return 0.0;
    }
    
    float intensity = 1.0 - (dist_to_line / width);
    intensity = pow(intensity + glow_radius * 5, 4.0); // Sharp falloff for laser effect
    
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
    float time_factor = time * 1.2;
    
    float total_intensity = 0.0;
    
    // Laser 1: Horizontal sweeping beam
    vec2 start1 = vec2(0.0, 0.5 + sin(time_factor * 0.4) * 0.3);
    vec2 end1 = vec2(1.0, 0.5 + sin(time_factor * 0.4 + 0.1) * 0.3);
    float width1 = 0.005 + audio_intensity * 0.003;
    float brightness1 = 0.8 + audio_intensity * 0.4;
    
    float laser1 = laser_beam_intensity(pos, start1, end1, width1, brightness1);
    total_intensity += laser1;
    
    // Laser 2: Vertical scanning beam
    vec2 start2 = vec2(0.3 + cos(time_factor * 0.6) * 0.2, 0.0);
    vec2 end2 = vec2(0.3 + cos(time_factor * 0.6) * 0.2, 1.0);
    float width2 = 0.004 + fft_sample * 0.004;
    float brightness2 = 0.7 + fft_sample * 0.5;
    
    float laser2 = laser_beam_intensity(pos, start2, end2, width2, brightness2);
    total_intensity += laser2 * 0.8;
    
    // Laser 3: Diagonal rotating beam
    float angle = time_factor * 0.5;
    vec2 center = vec2(0.5, 0.5);
    vec2 start3 = center + vec2(cos(angle), sin(angle)) * 0.4;
    vec2 end3 = center - vec2(cos(angle), sin(angle)) * 0.4;
    float width3 = 0.003 + audio_intensity * 0.002;
    float brightness3 = 0.6 + audio_intensity * 0.3;
    
    float laser3 = laser_beam_intensity(pos, start3, end3, width3, brightness3);
    total_intensity += laser3 * 0.9;
    
    // Laser 4: Audio-reactive cross pattern
    if (audio_intensity > 0.2) {
        // Horizontal cross beam
        vec2 start4h = vec2(0.0, 0.5);
        vec2 end4h = vec2(1.0, 0.5);
        float width4h = 0.002 + audio_intensity * 0.001;
        float brightness4h = audio_intensity * 0.8;
        
        float laser4h = laser_beam_intensity(pos, start4h, end4h, width4h, brightness4h);
        total_intensity += laser4h * 0.5;
        
        // Vertical cross beam
        vec2 start4v = vec2(0.5, 0.0);
        vec2 end4v = vec2(0.5, 1.0);
        float width4v = 0.002 + audio_intensity * 0.001;
        float brightness4v = audio_intensity * 0.8;
        
        float laser4v = laser_beam_intensity(pos, start4v, end4v, width4v, brightness4v);
        total_intensity += laser4v * 0.5;
    }
    
    // Add laser glow effect
    if (total_intensity > 0.1) {
        total_intensity += total_intensity * 0.2; // Slight glow
    }
    
    // Flickering effect for realism
    float flicker = sin(time * 30.0 + audio_intensity * 20.0) * 0.05 + 0.95;
    total_intensity *= flicker;
    
    return clamp(total_intensity, 0.0, 1.0);
}

// 1D compatibility function
float compute_waveform_at_x(float x_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Simple 1D laser scanning effect
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Scanning laser position
    float scan_pos = sin(time * 2.0) * 0.5 + 0.5;
    float dist = abs(x_coord - scan_pos);
    
    // Sharp laser beam
    float beam_width = 0.02 + fft_sample * 0.01;
    float intensity = smoothstep(beam_width, 0.0, dist);
    intensity = pow(intensity, 3.0); // Sharp falloff
    
    intensity *= (0.8 + audio_sample * 0.4);
    
    return clamp(intensity, 0.0, 1.0);
}
