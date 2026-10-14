// KarmaViz waveform "dark_matter_web" (cosmic): Dark matter cosmic web structure
// From KarmaViz 67e8a15 waveforms/cosmic/dark_matter_web.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    float web = 0.0;
    
    // Dark matter halos
    for (int i = 0; i < 10; i++) {
        float fi = float(i);
        float sample = texture(waveform_data, vec2(fi / 10.0, 0.5)).r;
        
        // Halo positions
        float halo_x = fract(sin(fi * 12.9898) * 43758.5453);
        float halo_y = fract(sin(fi * 78.233) * 43758.5453);
        
        float dist = distance(vec2(x_coord, y_coord), vec2(halo_x, halo_y));
        
        // Dark matter density profile (NFW-like)
        float halo_mass = abs(sample);
        float density = halo_mass / (dist * (1.0 + dist) * (1.0 + dist));
        web += density * 0.1;
        
        // Filaments connecting halos
        for (int j = i + 1; j < 10; j++) {
            float fj = float(j);
            float other_x = fract(sin(fj * 12.9898) * 43758.5453);
            float other_y = fract(sin(fj * 78.233) * 43758.5453);
            
            float halo_separation = distance(vec2(halo_x, halo_y), vec2(other_x, other_y));
            
            if (halo_separation < 0.4) {
                // Distance to filament
                vec2 line_start = vec2(halo_x, halo_y);
                vec2 line_end = vec2(other_x, other_y);
                vec2 point = vec2(x_coord, y_coord);
                
                float t = clamp(dot(point - line_start, line_end - line_start) / 
                               dot(line_end - line_start, line_end - line_start), 0.0, 1.0);
                float line_dist = distance(point, mix(line_start, line_end, t));
                
                // Filament density
                float filament_strength = halo_mass * abs(sample) / halo_separation;
                web += exp(-line_dist * 30.0) * filament_strength * 0.05;
            }
        }
    }
    
    // Cosmic voids (regions of low density)
    float void_effect = sin(x_coord * 50) * sin(y_coord * 55.14159) * 0.3;
    web = fract(void_effect);
    
    return clamp(web, 0.0, 0.1);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
