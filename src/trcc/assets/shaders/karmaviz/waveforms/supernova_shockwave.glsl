// KarmaViz waveform "supernova_shockwave" (cosmic): Supernova explosion shockwave
// From KarmaViz 67e8a15 waveforms/cosmic/supernova_shockwave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Supernova center
    float center_x = 0.5;
    float center_y = 0.5;
    float dist = distance(vec2(x_coord, y_coord), vec2(center_x, center_y));
    
    // Audio-driven explosion intensity
    float sample = texture(waveform_data, vec2(0.5, 0.5)).r;
    float explosion_strength = abs(sample);
    
    // Expanding shockwave
    float shockwave_radius = time * 0.2 * (1.0 + explosion_strength);
    float shockwave_thickness = 0.05 + explosion_strength * 0.03;
    
    // Main shockwave
    float shockwave = exp(-pow(abs(dist - shockwave_radius) / shockwave_thickness, 2.0));
    
    // Secondary shockwaves
    float secondary1 = exp(-pow(abs(dist - shockwave_radius * 0.7) / (shockwave_thickness * 0.5), 2.0)) * 0.6;
    float secondary2 = exp(-pow(abs(dist - shockwave_radius * 1.3) / (shockwave_thickness * 0.3), 2.0)) * 0.4;
    
    // Core remnant
    float core = exp(-dist * 20.0) * explosion_strength;
    
    // Ejecta patterns
    float ejecta = sin(atan(y_coord - center_y, x_coord - center_x) * 8.0 + time * 2.0) * 0.2 + 0.8;
    
    float total = (shockwave + secondary1 + secondary2 + core) * ejecta;
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
