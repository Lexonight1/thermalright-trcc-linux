// KarmaViz warp map "mobius_strip" (mathematical): Möbius strip topology transformation
// From KarmaViz 67e8a15 warp_maps/mathematical/mobius_strip.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Möbius Strip Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    
    float angle = atan(p.y, p.x);
    float radius = length(p);
    
    // Möbius transformation
    float mobius_angle = angle + sin(angle * 0.5 + t * 0.5) * 0.5;
    float mobius_radius = radius * (1.0 + 0.2 * cos(angle * 0.5 + t * 0.3));
    
    vec2 mobius_pos = vec2(cos(mobius_angle), sin(mobius_angle)) * mobius_radius;
    
    return (mobius_pos - p) * 0.3;
}
