// KarmaViz warp map "New Warp Map" (basic): A new custom warp map
// From KarmaViz 67e8a15 warp_maps/basic/new_warp_map.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Circular Waveform Warp Map
// This shader creates a circular waveform visualization using the audio data

vec2 get_pattern(vec2 pos, float t) {
    // Center the coordinates
    vec2 center = vec2(0.5, 0.5);
    vec2 centered_pos = pos - center;
    
    // Convert to polar coordinates
    float angle = atan(centered_pos.y, centered_pos.x);
    float radius = length(centered_pos);
    
    // Normalize angle to [0, 1] for waveform sampling
    // atan returns [-π, π], so we convert to [0, 1]
    float normalized_angle = (angle + 3.14159) / (2.0 * 3.14159);
    
    // Sample the waveform data at this angle
    float waveform_sample = texture(waveform_data, vec2(normalized_angle, 0.5)).r;
    
    // Scale the waveform sample
    float waveform_amplitude = waveform_sample * waveform_scale * 0.1;
    
    // Create circular waveform displacement
    // Push/pull points radially based on waveform data
    float target_radius = 0.3 + waveform_amplitude; // Base radius + waveform
    
    // Calculate displacement towards target radius
    float radius_diff = target_radius - radius;
    
    // Create radial displacement vector
    vec2 radial_direction = normalize(centered_pos);
    if (length(centered_pos) < 0.001) {
        radial_direction = vec2(1.0, 0.0); // Avoid division by zero at center
    }
    
    // Apply displacement with smooth falloff
    float falloff = smoothstep(0.0, 0.5, radius) * smoothstep(0.8, 0.4, radius);
    vec2 displacement = radial_direction * radius_diff * falloff * 0.5;
    
    return displacement;
}

// Alternative version: Spiral waveform
/*
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 centered_pos = pos - center;
    
    float angle = atan(centered_pos.y, centered_pos.x);
    float radius = length(centered_pos);
    
    // Create spiral sampling
    float spiral_factor = radius * 4.0; // How many times to wrap around
    float normalized_angle = (angle + 3.14159) / (2.0 * 3.14159);
    float spiral_sample_pos = fract(normalized_angle + spiral_factor);
    
    // Sample waveform
    float waveform_sample = texture(waveform_data, vec2(spiral_sample_pos, 0.5)).r;
    float waveform_amplitude = waveform_sample * waveform_scale * 0.05;
    
    // Create spiral displacement
    vec2 tangent = vec2(-centered_pos.y, centered_pos.x); // Perpendicular to radius
    tangent = normalize(tangent);
    
    float falloff = smoothstep(0.0, 0.4, radius) * smoothstep(0.7, 0.3, radius);
    vec2 displacement = tangent * waveform_amplitude * falloff;
    
    return displacement;
}
*/

// Alternative version: Animated rotating waveform
/*
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 centered_pos = pos - center;
    
    float angle = atan(centered_pos.y, centered_pos.x);
    float radius = length(centered_pos);
    
    // Add rotation animation
    float rotation_speed = 0.5;
    float animated_angle = angle + t * rotation_speed;
    
    // Normalize for sampling
    float normalized_angle = (animated_angle + 3.14159) / (2.0 * 3.14159);
    normalized_angle = fract(normalized_angle); // Keep in [0,1] range
    
    // Sample waveform
    float waveform_sample = texture(waveform_data, vec2(normalized_angle, 0.5)).r;
    float waveform_amplitude = waveform_sample * waveform_scale * 0.08;
    
    // Create radial pulsing effect
    vec2 radial_direction = normalize(centered_pos);
    if (length(centered_pos) < 0.001) {
        radial_direction = vec2(1.0, 0.0);
    }
    
    // Pulse effect with distance falloff
    float pulse_strength = sin(radius * 20.0 + t * 3.0) * 0.5 + 0.5;
    float falloff = smoothstep(0.0, 0.3, radius) * smoothstep(0.6, 0.2, radius);
    
    vec2 displacement = radial_direction * waveform_amplitude * pulse_strength * falloff;
    
    return displacement;
}
*/

// Alternative version: Multi-ring waveform
/*
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 centered_pos = pos - center;
    
    float angle = atan(centered_pos.y, centered_pos.x);
    float radius = length(centered_pos);
    
    // Create multiple concentric rings
    float num_rings = 3.0;
    float ring_index = floor(radius * num_rings);
    float ring_position = fract(radius * num_rings);
    
    // Sample different parts of waveform for each ring
    float angle_offset = ring_index * 0.33; // Offset each ring
    float normalized_angle = (angle + 3.14159) / (2.0 * 3.14159);
    float sample_pos = fract(normalized_angle + angle_offset);
    
    // Sample waveform
    float waveform_sample = texture(waveform_data, vec2(sample_pos, 0.5)).r;
    float waveform_amplitude = waveform_sample * waveform_scale * 0.06;
    
    // Create ring-based displacement
    vec2 radial_direction = normalize(centered_pos);
    if (length(centered_pos) < 0.001) {
        radial_direction = vec2(1.0, 0.0);
    }
    
    // Ring falloff
    float ring_falloff = smoothstep(0.0, 0.2, ring_position) * smoothstep(1.0, 0.8, ring_position);
    float distance_falloff = smoothstep(0.0, 0.2, radius) * smoothstep(0.7, 0.4, radius);
    
    vec2 displacement = radial_direction * waveform_amplitude * ring_falloff * distance_falloff;
    
    return displacement;
}
*/
