// KarmaViz warp map "warp_drive_bubble" (cosmic): Alcubierre warp drive spacetime distortion
// From KarmaViz 67e8a15 warp_maps/cosmic/warp_drive_bubble.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Warp Drive Bubble Warp Map
vec2 get_pattern(vec2 pos, float t) {
    // Warp bubble center (moving)
    vec2 bubble_center = vec2(0.3 + sin(t * 0.5) * 0.4, 0.5);
    vec2 to_bubble = pos - bubble_center;
    float dist = length(to_bubble);
    
    // Warp bubble parameters
    float bubble_radius = 0.15;
    float warp_factor = 2.0;
    
    vec2 warp = vec2(0.0);
    
    if (dist < bubble_radius) {
        // Inside bubble - space contracts in front, expands behind
        float bubble_phase = atan(to_bubble.y, to_bubble.x);
        float front_back = cos(bubble_phase);
        
        // Contraction/expansion
        float space_distortion = front_back * warp_factor * (1.0 - dist / bubble_radius);
        warp = normalize(to_bubble) * space_distortion * 0.02;
        
        // Bubble wall effects
        float wall_dist = abs(dist - bubble_radius * 0.8);
        if (wall_dist < 0.02) {
            warp += normalize(to_bubble) * sin(t * 10.0) * 0.005;
        }
    } else {
        // Outside bubble - spacetime wake
        float wake_strength = exp(-(dist - bubble_radius) * 10.0);
        vec2 wake_dir = vec2(sin(t * 0.5), 0.0); // Direction of travel
        warp = wake_dir * wake_strength * 0.01;
    }
    
    return warp;
}
