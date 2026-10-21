// KarmaViz warp map "cosmic_string" (cosmic): Cosmic string spacetime defect
// From KarmaViz 67e8a15 warp_maps/cosmic/cosmic_string.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Cosmic String Warp Map
vec2 get_pattern(vec2 pos, float t) {
    // Cosmic string runs vertically through space
    float string_x = 0.5 + sin(t * 0.2) * 0.1;
    float dist_to_string = abs(pos.x - string_x);
    
    vec2 warp = vec2(0.0);
    
    // String tension creates conical spacetime
    float string_strength = 0.1 / (dist_to_string + 0.01);
    
    // Angular deficit around string
    float angle_deficit = string_strength * 0.1;
    
    // Warp space around the string
    if (pos.x < string_x) {
        // Left side
        warp.x = -angle_deficit * (pos.y - 0.5);
        warp.y = angle_deficit * (pos.x - string_x);
    } else {
        // Right side
        warp.x = angle_deficit * (pos.y - 0.5);
        warp.y = -angle_deficit * (pos.x - string_x);
    }
    
    // String oscillations
    float oscillation = sin(pos.y * 10.0 + t * 3.0) * 0.01;
    warp.x += oscillation * string_strength;
    
    return warp;
}
