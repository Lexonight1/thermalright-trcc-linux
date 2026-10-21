// KarmaViz waveform "nebula_cloud" (cosmic): Interstellar nebula gas clouds
// From KarmaViz 67e8a15 waveforms/cosmic/nebula_cloud.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float nebula = 0.0;
    
    // Multiple gas cloud layers
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 6.0, 0.5)).r;
        
        // Cloud center
        float cloud_x = 0.3 + 0.4 * sin(fi * 2.1 + time * 0.1);
        float cloud_y = 0.3 + 0.4 * cos(fi * 1.7 + time * 0.08);
        
        float dist = distance(vec2(x_coord, y_coord), vec2(cloud_x, cloud_y));
        
        // Gas density
        float density = exp(-dist * (2.0 + fi)) * abs(sample);
        
        // Turbulence
        float turbulence = sin(x_coord * 10.0 + fi) * cos(y_coord * 8.0 + fi) * 0.2;
        density *= (1.0 + turbulence);
        
        // Stellar winds
        float wind = sin(dist * 15.0 - time * 3.0 + fi) * 0.1;
        density *= (1.0 + wind);
        
        nebula += density;
    }
    
    return clamp(nebula, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
