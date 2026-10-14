// KarmaViz warp map "Elastic Bounce" (motion): Bouncing elastic objects with wave propagation and medium vibrations
// From KarmaViz 67e8a15 warp_maps/motion/elastic_bounce.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Simulate elastic bouncing balls
    vec2 ball1_pos = vec2(0.3, 0.5 + sin(t * 3.0) * 0.3);
    vec2 ball2_pos = vec2(0.7, 0.4 + cos(t * 2.5 + 1.0) * 0.35);
    vec2 ball3_pos = vec2(0.5, 0.6 + sin(t * 4.0 + 2.0) * 0.25);
    
    vec2 total_elastic_force = vec2(0.0);
    
    // Process each bouncing ball
    vec2 balls[3] = vec2[3](ball1_pos, ball2_pos, ball3_pos);
    float ball_radii[3] = float[3](0.15, 0.12, 0.18);
    
    for (int i = 0; i < 3; i++) {
        vec2 ball_pos = balls[i];
        float ball_radius = ball_radii[i];
        
        vec2 to_ball = ball_pos - pos;
        float dist = length(to_ball);
        
        // Elastic force (repulsive when close, attractive when far)
        if (dist < ball_radius) {
            // Inside ball - strong repulsive force
            vec2 repulsion = -normalize(to_ball) * (ball_radius - dist) / ball_radius;
            total_elastic_force += repulsion * 2.0;
        } else if (dist < ball_radius * 2.0) {
            // Near ball - elastic attraction
            vec2 attraction = normalize(to_ball) * (dist - ball_radius) / ball_radius * 0.5;
            total_elastic_force += attraction;
        }
        
        // Add bounce wave effects
        float bounce_phase = t * (3.0 + float(i) * 0.5);
        float bounce_wave = sin(dist * 8.0 - bounce_phase) * exp(-dist * 2.0);
        vec2 wave_direction = normalize(to_ball);
        total_elastic_force += wave_direction * bounce_wave * 0.3;
    }
    
    // Add elastic medium vibrations
    float medium_vibration = sin(pos.x * 10.0 + t * 4.0) * cos(pos.y * 8.0 + t * 3.0);
    total_elastic_force += vec2(medium_vibration * 0.1, -medium_vibration * 0.05);
    
    return total_elastic_force * 0.02;
}
