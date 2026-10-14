// KarmaViz waveform "stepped_radial_waves" (digital): Stepped wave patterns that radiate outward from center
// From KarmaViz 67e8a15 waveforms/digital/stepped_radial_waves.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Calculate distance from center
    vec2 center = vec2(0.5, 0.5);
    float dist = distance(vec2(x_coord, y_coord), center);
    
    // Use distance as sample coordinate (wrapped)
    float sample_coord = fract(dist * 4.0); // 4 rings
    float sample = texture(waveform_data, vec2(sample_coord, 0.5)).r;
    
    // Create stepped levels
    float stepped_sample = floor(sample * 8.0) / 8.0;
    
    // Create concentric rings with stepped intensity
    float ring_width = 0.02;
    float ring_index = floor(dist / ring_width);
    float ring_pos = fract(dist / ring_width);
    
    // Modulate ring intensity with stepped audio data
    float intensity = stepped_sample * waveform_scale;
    
    // Create ring pattern
    if (ring_pos < 0.5) {
        return intensity;
    }
    
    return intensity * 0.3; // Dimmer between rings
}
