// KarmaViz warp map "Pendulum Swing" (motion): Creates a pendulum-like swinging motion across the screen
// From KarmaViz 67e8a15 warp_maps/motion/pendulum_swing.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Pendulum motion parameters
    float pendulum_length = 0.8;
    float gravity = 9.81;
    float frequency = sqrt(gravity / pendulum_length);
    
    // Pendulum angle (oscillates between -pi/4 and pi/4)
    float angle = sin(t * frequency) * 0.785398; // pi/4 radians
    
    // Calculate pendulum position
    float pendulum_x = sin(angle) * pendulum_length;
    float pendulum_y = cos(angle) * pendulum_length;
    
    // Distance from pendulum bob
    vec2 bob_pos = vec2(0.5 + pendulum_x * 0.3, 0.2 + pendulum_y * 0.3);
    float dist = distance(pos, bob_pos);
    
    // Create influence field around pendulum
    float influence = exp(-dist * 8.0);
    
    // Direction of pendulum motion
    float velocity = cos(t * frequency) * frequency;
    vec2 motion_dir = vec2(cos(angle + 1.5708), -sin(angle + 1.5708)); // perpendicular to pendulum
    
    return motion_dir * influence * velocity * 0.01;
}
