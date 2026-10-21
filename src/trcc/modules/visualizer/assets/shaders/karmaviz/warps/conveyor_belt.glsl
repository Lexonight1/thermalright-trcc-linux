// KarmaViz warp map "Conveyor Belt" (motion): Industrial conveyor system with alternating directions and vibrations
// From KarmaViz 67e8a15 warp_maps/motion/conveyor_belt.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Create multiple conveyor belt systems
    float belt_width = 0.15;
    float belt_spacing = 0.25;
    
    // Determine which belt we're on
    float belt_y = floor(pos.y / belt_spacing) * belt_spacing + belt_spacing * 0.5;
    float distance_to_belt = abs(pos.y - belt_y);
    
    // Belt influence decreases with distance
    float belt_influence = exp(-distance_to_belt / belt_width * 4.0);
    
    // Alternating belt directions
    float belt_direction = mod(floor(pos.y / belt_spacing), 2.0) * 2.0 - 1.0;
    
    // Belt speed varies with time
    float belt_speed = 1.5 + sin(t * 1.5) * 0.5;
    
    // Horizontal motion along belt
    vec2 belt_motion = vec2(belt_direction * belt_speed, 0.0) * belt_influence;
    
    // Add mechanical vibration
    float vibration_freq = 15.0;
    vec2 vibration = vec2(
        sin(pos.x * vibration_freq + t * 8.0) * 0.1,
        cos(pos.y * vibration_freq + t * 6.0) * 0.05
    ) * belt_influence;
    
    // Add transfer points between belts
    float transfer_zone = smoothstep(0.05, 0.1, distance_to_belt);
    vec2 transfer_motion = vec2(0.0, sin(t * 3.0 + pos.x * 5.0) * 0.3) * (1.0 - transfer_zone);
    
    return (belt_motion + vibration + transfer_motion) * 0.02;
}
