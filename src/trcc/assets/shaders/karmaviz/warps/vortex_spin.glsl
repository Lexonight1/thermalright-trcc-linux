// KarmaViz warp map "Vortex Spin" (distortion): Creates a spinning vortex effect that pulls towards the center
// From KarmaViz 67e8a15 warp_maps/distortion/vortex_spin.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    float dist = length(offset);
    
    // Create vortex rotation
    float angle = atan(offset.y, offset.x) + t * 2.0 + dist * 8.0;
    float radius = dist * (1.0 + sin(t * 1.5) * 0.1);
    
    vec2 rotated = vec2(cos(angle), sin(angle)) * radius;
    
    return (rotated - offset) * 0.03;
}
