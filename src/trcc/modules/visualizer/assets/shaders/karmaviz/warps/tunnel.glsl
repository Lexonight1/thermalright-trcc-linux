// KarmaViz warp map "Tunnel" (geometric): Creates a tunnel effect with perspective distortion and radial patterns
// From KarmaViz 67e8a15 warp_maps/geometric/tunnel.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Tunnel effect with perspective distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = pos - 0.5;
    float dist = length(center);
    float angle = atan(center.y, center.x);
    
    // Create tunnel effect
    float tunnel_depth = 1.0 / (dist + 0.1) - t * 2.0;
    float tunnel_x = sin(angle * 8.0 + tunnel_depth) * 0.02;
    float tunnel_y = cos(angle * 8.0 + tunnel_depth) * 0.02;
    
    return vec2(tunnel_x, tunnel_y);
}
