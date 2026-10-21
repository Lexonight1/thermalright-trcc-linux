// KarmaViz warp map "Magnetic Field" (motion): Electromagnetic field lines with pole dynamics and oscillations
// From KarmaViz 67e8a15 warp_maps/motion/magnetic_field.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Simulate magnetic field lines between poles
    vec2 north_pole = vec2(0.3, 0.7);
    vec2 south_pole = vec2(0.7, 0.3);
    
    // Animate pole positions
    north_pole += vec2(sin(t * 0.3), cos(t * 0.4)) * 0.1;
    south_pole += vec2(cos(t * 0.2), sin(t * 0.5)) * 0.1;
    
    vec2 to_north = north_pole - pos;
    vec2 to_south = south_pole - pos;
    
    float dist_north = length(to_north);
    float dist_south = length(to_south);
    
    // Magnetic field strength (inverse distance)
    vec2 field_north = normalize(to_north) / (dist_north + 0.1);
    vec2 field_south = -normalize(to_south) / (dist_south + 0.1);
    
    vec2 magnetic_field = field_north + field_south;
    
    // Add field line curvature
    float field_angle = atan(magnetic_field.y, magnetic_field.x);
    vec2 curved_field = vec2(
        cos(field_angle + sin(t + length(magnetic_field)) * 0.5),
        sin(field_angle + sin(t + length(magnetic_field)) * 0.5)
    );
    
    // Add electromagnetic oscillations
    float em_frequency = 8.0;
    float em_wave = sin(pos.x * em_frequency + t * 4.0) * cos(pos.y * em_frequency + t * 3.0);
    
    vec2 em_motion = vec2(
        cos(t * 2.0 + em_wave) * 0.2,
        sin(t * 1.8 + em_wave) * 0.2
    );
    
    return (curved_field * 0.5 + em_motion) * 0.025;
}
