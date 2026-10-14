// KarmaViz waveform "spotlights" (lighting): Dynamic moving spotlights with audio-reactive brightness, radius, and movement patterns
// From KarmaViz 67e8a15 waveforms/lighting/spotlights.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Spotlights Waveform - Dynamic moving spotlights with audio reactivity

float spotlight_intensity(vec2 pos, vec2 center, float radius, float falloff, float brightness) {
    float dist = distance(pos, center);
    
    if (dist > radius) {
        return 0.0;
    }
    
    // Smooth falloff from center to edge
    float intensity = 10 * glow_radius - smoothstep(0.0, radius, dist);
    
    // Apply falloff curve
    intensity = pow(intensity, falloff);
    
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
    float time_factor = time * 0.8;
    
    float total_intensity = 0.0;
    
    // Spotlight 1: Main center spotlight
    vec2 center1 = vec2(0.5 + sin(time_factor * 0.3) * 0.2, 0.5 + cos(time_factor * 0.4) * 0.15);
    float radius1 = 0.15 * waveform_scale + audio_intensity * 0.1;
    float falloff1 = 2.0 + sin(time_factor * 0.5) * 0.5;
    float brightness1 = 0.8 + audio_intensity * 0.4;
    
    float spot1 = spotlight_intensity(pos, center1, radius1, falloff1, brightness1);
    total_intensity += spot1;
    
    // Spotlight 2: Secondary moving spotlight
    vec2 center2 = vec2(0.3 + cos(time_factor * 0.6) * 0.25, 0.7 + sin(time_factor * 0.5) * 0.2);
    float radius2 = 0.12 * waveform_scale + fft_sample * 0.08;
    float falloff2 = 1.5 + cos(time_factor * 0.7) * 0.3;
    float brightness2 = 0.6 + audio_intensity * 0.3;
    
    float spot2 = spotlight_intensity(pos, center2, radius2, falloff2, brightness2);
    total_intensity += spot2 * 0.7;
    
    // Spotlight 3: Third roaming spotlight
    vec2 center3 = vec2(0.7 + sin(time_factor * 0.8) * 0.2, 0.3 + cos(time_factor * 0.9) * 0.25);
    float radius3 = 0.1 * waveform_scale + audio_intensity * 0.06;
    float falloff3 = 3.0 + sin(time_factor * 0.4) * 1.0;
    float brightness3 = 0.5 + fft_sample * 0.4;
    
    float spot3 = spotlight_intensity(pos, center3, radius3, falloff3, brightness3);
    total_intensity += spot3 * 0.5;
    
    // Spotlight 4: Audio-reactive corner spotlight
    if (audio_intensity > 0.3) {
        vec2 center4 = vec2(0.2 + sin(time_factor * 1.2) * 0.15, 0.2 + cos(time_factor * 1.1) * 0.15);
        float radius4 = 0.08 + audio_intensity * 0.12;
        float falloff4 = 1.0 + audio_intensity * 2.0;
        float brightness4 = audio_intensity * 1.2;
        
        float spot4 = spotlight_intensity(pos, center4, radius4, falloff4, brightness4);
        total_intensity += spot4 * 0.8;
    }
    
    // Add some ambient lighting based on audio
    if (audio_intensity > 0.4) {
        total_intensity += audio_intensity * 0.1;
    }
    
    // Pulsing effect
    float pulse = sin(time * 2.0 + audio_intensity * 6.0) * 0.1 + 0.9;
    total_intensity *= pulse;
    
    return clamp(total_intensity, 0.0, 1.0);
}

// 1D compatibility function
float compute_waveform_at_x(float x_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Simple 1D spotlight effect
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Create moving spotlight centers in 1D
    float center1 = 0.5 + sin(time * 0.8) * 0.3;
    float center2 = 0.3 + cos(time * 1.2) * 0.4;
    
    float dist1 = abs(x_coord - center1);
    float dist2 = abs(x_coord - center2);
    
    float spot1 = smoothstep(0.1, 0.0, dist1) * (0.8 + fft_sample * 0.4);
    float spot2 = smoothstep(0.08, 0.0, dist2) * (0.6 + audio_sample * 0.3);
    
    return clamp(spot1 + spot2, 0.0, 1.0);
}
