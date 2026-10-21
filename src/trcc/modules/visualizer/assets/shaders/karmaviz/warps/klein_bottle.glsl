// KarmaViz warp map "klein_bottle" (mathematical): Klein bottle surface projection
// From KarmaViz 67e8a15 warp_maps/mathematical/klein_bottle.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Klein Bottle Warp Map
vec2 get_pattern(vec2 pos, float t) {
    float u = pos.x * 6.28318; // 0 to 2π
    float v = pos.y * 6.28318; // 0 to 2π
    
    // Klein bottle parametric equations (simplified 2D projection)
    float x = cos(u) * (1.0 + sin(v) * sin(u * 0.5));
    float y = sin(u) * (1.0 + sin(v) * sin(u * 0.5)) + cos(v) * cos(u * 0.5);
    
    // Animate the bottle
    x += sin(t * 0.5) * 0.2;
    y += cos(t * 0.3) * 0.2;
    
    // Normalize and create displacement
    vec2 klein_pos = vec2(x, y) * 0.1;
    
    return klein_pos - (pos - vec2(0.5)) * 0.2;
}
