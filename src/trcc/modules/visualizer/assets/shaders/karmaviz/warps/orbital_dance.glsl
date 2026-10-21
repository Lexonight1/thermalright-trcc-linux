// KarmaViz warp map "Orbital Dance" (motion): Multiple objects orbiting in complex patterns creating gravitational-like distortions
// From KarmaViz 67e8a15 warp_maps/motion/orbital_dance.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 total_force = vec2(0.0);
    
    // Orbit 1: Large slow orbit
    float angle1 = t * 0.5;
    vec2 orbit1 = center + vec2(cos(angle1), sin(angle1)) * 0.3;
    float dist1 = distance(pos, orbit1);
    float force1 = 1.0 / (dist1 * dist1 + 0.01);
    vec2 dir1 = normalize(orbit1 - pos);
    total_force += dir1 * force1 * 0.001;
    
    // Orbit 2: Medium speed orbit
    float angle2 = t * 1.2 + 2.094; // offset by 2π/3
    vec2 orbit2 = center + vec2(cos(angle2), sin(angle2)) * 0.2;
    float dist2 = distance(pos, orbit2);
    float force2 = 1.0 / (dist2 * dist2 + 0.01);
    vec2 dir2 = normalize(orbit2 - pos);
    total_force += dir2 * force2 * 0.001;
    
    // Orbit 3: Fast small orbit
    float angle3 = t * 2.0 + 4.189; // offset by 4π/3
    vec2 orbit3 = center + vec2(cos(angle3), sin(angle3)) * 0.15;
    float dist3 = distance(pos, orbit3);
    float force3 = 1.0 / (dist3 * dist3 + 0.01);
    vec2 dir3 = normalize(orbit3 - pos);
    total_force += dir3 * force3 * 0.001;
    
    // Add some rotational component
    vec2 to_center = pos - center;
    vec2 tangent = vec2(-to_center.y, to_center.x);
    float rotation_strength = sin(t * 0.8) * 0.5;
    total_force += tangent * rotation_strength * 0.002;
    
    return total_force;
}
