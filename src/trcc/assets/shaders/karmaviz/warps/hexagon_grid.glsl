// KarmaViz warp map "Hexagon Grid" (geometric): Creates a hexagonal grid distortion pattern
// From KarmaViz 67e8a15 warp_maps/geometric/hexagon_grid.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Hexagonal grid coordinates
    float scale = 8.0;
    vec2 hex_pos = pos * scale;
    
    // Convert to hexagonal coordinates
    float q = hex_pos.x;
    float r = (hex_pos.y - hex_pos.x * 0.5) / 0.866025;
    
    // Round to nearest hex center
    float q_round = round(q);
    float r_round = round(r);
    float s_round = round(-q - r);
    
    // Calculate offset from hex center
    vec2 hex_center = vec2(q_round, r_round * 0.866025 + q_round * 0.5) / scale;
    vec2 offset = pos - hex_center;
    
    // Animate the distortion
    float pulse = sin(t * 2.0 + length(hex_center) * 10.0) * 0.5 + 0.5;
    
    return offset * pulse * 0.02;
}
