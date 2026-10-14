// KarmaViz warp map "galactic_rotation" (cosmic): Galaxy rotation curve effects
// From KarmaViz 67e8a15 warp_maps/cosmic/galactic_rotation.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Galactic Rotation Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 to_center = pos - center;
    float radius = length(to_center);
    
    // Galactic rotation curve
    float rotation_speed;
    if (radius < 0.1) {
        // Solid body rotation in center
        rotation_speed = radius * 2.0;
    } else {
        // Flat rotation curve (dark matter effect)
        rotation_speed = 0.2;
    }
    
    // Rotation angle
    float rotation_angle = rotation_speed * t;
    
    // Spiral arm pattern
    float spiral_angle = atan(to_center.y, to_center.x) + 2.0 * log(radius + 0.1);
    float arm_strength = sin(spiral_angle * 2.0) * 0.5 + 0.5;
    
    // Differential rotation creates shear
    float shear = rotation_speed * 0.1;
    
    // Perpendicular component for rotation
    vec2 perp = vec2(-to_center.y, to_center.x);
    perp = normalize(perp);
    
    vec2 warp = perp * shear * arm_strength;
    
    return warp;
}
