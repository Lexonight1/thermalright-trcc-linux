// KarmaViz waveform "wormhole_portal" (cosmic): Spacetime wormhole visualization
// From KarmaViz 67e8a15 waveforms/cosmic/wormhole_portal.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Wormhole center
    float center_x = 0.5;
    float center_y = 0.5;
    float dist = distance(vec2(x_coord, y_coord), vec2(center_x, center_y));
    
    // Audio modulation
    float sample = texture(waveform_data, vec2(dist, 0.5)).r;
    
    // Throat of the wormhole
    float throat_radius = 0.1 + abs(sample) * 0.05;
    
    // Spacetime curvature
    float curvature = 1.0 / (1.0 + dist * dist * 10.0);
    
    // Event horizon distortion
    float distortion = sin(dist * 20.0 - time * 5.0) * curvature * 0.2;
    
    // Gravitational lensing effect
    float lensing = exp(-pow(abs(dist - throat_radius) / 0.02, 2.0));
    
    // Exotic matter rings
    float exotic_matter = 0.0;
    for (int i = 1; i <= 3; i++) {
        float ring_radius = throat_radius + float(i) * 0.08;
        exotic_matter += exp(-pow(abs(dist - ring_radius) / 0.01, 2.0)) * (1.0 / float(i));
    }
    
    // Tidal forces
    float tidal = sin(atan(y_coord - center_y, x_coord - center_x) * 4.0 + time * 3.0) * 0.1 + 0.9;
    
    float wormhole = (lensing + exotic_matter * abs(sample)) * tidal * (1.0 + distortion);
    return clamp(wormhole, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
