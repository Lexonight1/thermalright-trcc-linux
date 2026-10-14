// KarmaViz warp map "Spiral Galaxy" (motion): Galactic rotation with spiral arms and decreasing velocity by distance
// From KarmaViz 67e8a15 warp_maps/motion/spiral_galaxy.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    float radius = length(offset);
    float angle = atan(offset.y, offset.x);
    
    // Galaxy rotation speed decreases with distance
    float rotation_speed = 2.0 / (1.0 + radius * 3.0);
    
    // Create spiral arms
    float spiral_angle = angle + log(radius + 0.1) * 2.0 + t * rotation_speed;
    float arm_strength = sin(spiral_angle * 3.0) * 0.5 + 0.5;
    
    // Tangential velocity for rotation
    vec2 tangent = vec2(-offset.y, offset.x);
    if (radius > 0.01) {
        tangent = normalize(tangent);
    }
    
    // Radial component for spiral structure
    vec2 radial = normalize(offset);
    
    // Combine rotational and spiral motion
    vec2 motion = tangent * rotation_speed * arm_strength + radial * sin(t + radius * 5.0) * 0.2;
    
    // Add galactic turbulence
    float turbulence = sin(pos.x * 8.0 + t) * cos(pos.y * 6.0 + t * 1.2) * 0.1;
    motion += vec2(turbulence, -turbulence * 0.5);
    
    return motion * 0.025;
}
