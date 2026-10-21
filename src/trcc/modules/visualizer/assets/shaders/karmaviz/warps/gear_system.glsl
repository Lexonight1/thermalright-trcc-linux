// KarmaViz warp map "Gear System" (motion): Interconnected mechanical gears with rotational motion and teeth effects
// From KarmaViz 67e8a15 warp_maps/motion/gear_system.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Define gear centers and radii
    vec2 gear1_center = vec2(0.25, 0.5);
    vec2 gear2_center = vec2(0.5, 0.5);
    vec2 gear3_center = vec2(0.75, 0.5);
    
    float gear1_radius = 0.15;
    float gear2_radius = 0.12;
    float gear3_radius = 0.18;
    
    // Gear rotation speeds (interconnected)
    float base_speed = t * 2.0;
    float gear1_rotation = base_speed;
    float gear2_rotation = -base_speed * (gear1_radius / gear2_radius);
    float gear3_rotation = base_speed * (gear1_radius / gear3_radius);
    
    vec2 total_gear_motion = vec2(0.0);
    
    // Process each gear
    vec2 gear_centers[3] = vec2[3](gear1_center, gear2_center, gear3_center);
    float gear_radii[3] = float[3](gear1_radius, gear2_radius, gear3_radius);
    float gear_rotations[3] = float[3](gear1_rotation, gear2_rotation, gear3_rotation);
    
    for (int i = 0; i < 3; i++) {
        vec2 gear_center = gear_centers[i];
        float gear_radius = gear_radii[i];
        float gear_rotation = gear_rotations[i];
        
        vec2 to_gear = pos - gear_center;
        float dist = length(to_gear);
        
        if (dist < gear_radius && dist > 0.01) {
            // Inside gear - rotational motion
            float angle = atan(to_gear.y, to_gear.x) + gear_rotation;
            vec2 rotational_velocity = vec2(-sin(angle), cos(angle)) * gear_radius;
            
            // Add gear teeth effects
            float teeth_count = 12.0;
            float tooth_angle = angle * teeth_count;
            float tooth_effect = sin(tooth_angle) * 0.1;
            
            vec2 gear_motion = rotational_velocity + normalize(to_gear) * tooth_effect;
            total_gear_motion += gear_motion * (1.0 - dist / gear_radius);
        } else if (dist < gear_radius * 1.5) {
            // Near gear - influence field
            float influence = exp(-(dist - gear_radius) * 5.0);
            vec2 tangent = vec2(-to_gear.y, to_gear.x);
            if (dist > 0.01) {
                tangent = normalize(tangent);
            }
            total_gear_motion += tangent * influence * 0.3;
        }
    }
    
    // Add mechanical vibration
    float mech_freq = 20.0;
    vec2 vibration = vec2(
        sin(t * mech_freq) * 0.05,
        cos(t * mech_freq * 1.3) * 0.03
    );
    
    return (total_gear_motion + vibration) * 0.015;
}
