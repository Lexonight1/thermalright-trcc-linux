// KarmaViz waveform "jellyfish_pulse" (organic): A graceful jellyfish waveform with a translucent pulsing bell and flowing tentacles that sway with the music. Creates mesmerizing bioluminescent effects.
// From KarmaViz 67e8a15 waveforms/organic/jellyfish_pulse.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Jellyfish Pulse Waveform
// Creates graceful, pulsing jellyfish with flowing tentacles

// Pseudo-random number generator
float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// 2D noise function
float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    
    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));
    
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}

// Smooth distance function for jellyfish bell
float jellyfish_bell(vec2 pos, vec2 center, float radius, float pulse) {
    vec2 offset = pos - center;
    float dist = length(offset);
    
    // Create bell shape with pulsing
    float bell_radius = radius * (1.0 + pulse * 0.3);
    float bell_height = bell_radius * 0.6;
    
    // Elliptical bell shape
    float ellipse_dist = length(vec2(offset.x, offset.y * 1.5));
    float bell_intensity = 1.0 - smoothstep(0.0, bell_radius, ellipse_dist);
    
    // Add dome curvature
    if (offset.y > 0.0) {
        bell_intensity *= exp(-offset.y * 2.0);
    }
    
    return bell_intensity;
}

// Generate flowing tentacles
float jellyfish_tentacles(vec2 pos, vec2 center, float time, float audio_intensity) {
    vec2 offset = pos - center;
    
    // Only draw tentacles below the bell
    if (offset.y <= 0.0) {
        return 0.0;
    }
    
    float tentacle_intensity = 0.0;
    
    // Create multiple tentacles
    for (int i = 0; i < 4; i++) {
        float angle = float(i) * 0.205398; // 45 degrees apart
        float tentacle_phase = time * 2.0 + float(i) * 0.5;
        
        // Tentacle position with swaying motion
        vec2 tentacle_dir = vec2(cos(angle), sin(angle));
        float sway = sin(tentacle_phase + offset.y *40.0) * 0.1 * audio_intensity;
        tentacle_dir.x += sway;
        
        // Distance from tentacle center line
        float tentacle_x = dot(offset, vec2(-tentacle_dir.y, tentacle_dir.x));
        float tentacle_y = dot(offset, tentacle_dir);
        
        // Tentacle width decreases with distance
        float tentacle_width = 0.02 * (1.0 - tentacle_y * 0.9) * (1.0 + audio_intensity * 0.3);
        
        // Tentacle length varies with audio
        float max_length = 0.9 + audio_intensity * 0.1;
        
        if (tentacle_y > 0.0 && tentacle_y < max_length) {
            // Flowing motion along tentacle
            float flow = sin(tentacle_phase - tentacle_y * 8.0) * 0.5 + 0.5;
            float tentacle_dist = abs(tentacle_x);
            
            if (tentacle_dist < tentacle_width) {
                float intensity = (1.0 - tentacle_dist / tentacle_width) * flow;
                intensity *= (1.0 - tentacle_y / max_length); // Fade with distance
                tentacle_intensity += intensity * 0.3;
            }
        }
    }
    
    return min(tentacle_intensity, 1.0);
}

// Single jellyfish function
float single_jellyfish(vec2 pos, vec2 center, float size, float phase_offset, float bass, float mid, float treble) {
    vec2 offset = pos - center;
    
    // Pulsing based on bass frequencies and glow_radius
    float pulse_strength = bass * 0.3 + sin(time * 3.0 + phase_offset) * 0.1;
    float glow_effect = glow_radius * 0.5; // Use glow_radius to affect pulse
    
    // Bell intensity with size and glow scaling
    float bell_radius = size * waveform_scale * (0.08 + mid * 0.03 + glow_radius * 0.02);
    float bell_intensity = jellyfish_bell(pos, center, bell_radius, pulse_strength);
    
    // Tentacle intensity
    float tentacle_intensity = jellyfish_tentacles(pos, center, time + phase_offset, treble * size) * 3;
    
    // Combine bell and tentacles
    float total_intensity = max(bell_intensity * 0.6, tentacle_intensity);
    
    // Add bioluminescent glow affected by glow_radius
    float glow_dist = length(offset);
    float glow = exp(-glow_dist * (2.0 + glow_radius)) * treble * size * 0.15;
    total_intensity += glow;
    
    // Add organic texture variation
    float texture_variation = noise(pos * 6.0 + time * 0.3) * 0.05;
    total_intensity += texture_variation * mid * 0.2;
    
    // Pulsing transparency effect
    float transparency_pulse = sin(time * 2.0 + phase_offset + bass * 3.0) * 0.08 + 0.92;
    total_intensity *= transparency_pulse;
    
    return total_intensity * size * 0.8;
}

// Main jellyfish swarm function
float jellyfish_swarm(vec2 pos) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Get audio data for different frequency ranges
    float bass = 0.0;
    float mid = 0.0;
    float treble = 0.0;
    
    for (int i = 0; i < min(waveform_length, 8); i++) {
        float sample_x = float(i) / float(min(waveform_length, 8));
        float audio_level = texture(waveform_data, vec2(sample_x, 0.5)).r;
        
        if (i < 2) {
            bass += audio_level;
        } else if (i < 12) {
            mid += audio_level;
        } else {
            treble += audio_level;
        }
    }
    
    bass /= 6.0;
    mid /= 6.0;
    treble /= 6.0;
    
    float total_intensity = 0.0;
    
    // Create multiple jellyfish with different sizes and floating patterns
    
    // Large main jellyfish
    vec2 center1 = vec2(0.5 + sin(time * 0.8) * 0.15, 0.35 + sin(time * 1.2) * 0.08);
    total_intensity += single_jellyfish(pos, center1, 1.2, 0.0, bass, mid, treble);
    
    // Medium jellyfish 1
    vec2 center2 = vec2(0.25 + cos(time * 1.1) * 0.12, 0.6 + sin(time * 0.9) * 0.06);
    total_intensity += single_jellyfish(pos, center2, 0.8, 1.57, bass, mid, treble);
    
    // Medium jellyfish 2
    vec2 center3 = vec2(0.75 + sin(time * 1.3) * 0.1, 0.25 + cos(time * 1.0) * 0.07);
    total_intensity += single_jellyfish(pos, center3, 0.9, 3.14, bass, mid, treble);
    
    // Small jellyfish 1
    vec2 center4 = vec2(0.15 + sin(time * 1.6) * 0.08, 0.8 + cos(time * 1.4) * 0.04);
    total_intensity += single_jellyfish(pos, center4, 0.5, 4.71, bass, mid, treble);
    
    // Small jellyfish 2
    vec2 center5 = vec2(0.85 + cos(time * 1.8) * 0.06, 0.7 + sin(time * 1.1) * 0.05);
    total_intensity += single_jellyfish(pos, center5, 0.6, 2.36, bass, mid, treble);
    
    // Tiny jellyfish for ambiance
    vec2 center6 = vec2(0.35 + sin(time * 2.1) * 0.05, 0.15 + cos(time * 1.7) * 0.03);
    total_intensity += single_jellyfish(pos, center6, 0.3, 5.5, bass, mid, treble);
    
    return clamp(total_intensity, 0.0, 1.0) * 0.5;
}

// Main waveform function
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Generate jellyfish swarm
    vec2 pos = vec2(x_coord, y_coord);
    float jellyfish_intensity = jellyfish_swarm(pos);
    
    return jellyfish_intensity;
}

// Backward compatibility function
float compute_waveform_at_x(float x_coord) {
    return 0.0;
}
