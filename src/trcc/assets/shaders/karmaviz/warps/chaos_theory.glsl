// KarmaViz warp map "Chaos Theory" (mathematical): Hénon map, logistic map, Duffing oscillator, and butterfly effect visualizations
// From KarmaViz 67e8a15 warp_maps/mathematical/chaos_theory.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Chaos Theory and Strange Attractors
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Hénon map
    float a = 1.4 + sin(t * 0.1) * 0.2;
    float b = 0.3 + cos(t * 0.15) * 0.1;

    vec2 henon_pos = centered * 3.0;
    for (int i = 0; i < 3; i++) {
        float x_new = 1.0 - a * henon_pos.x * henon_pos.x + henon_pos.y;
        float y_new = b * henon_pos.x;
        henon_pos = vec2(x_new, y_new);
    }
    vec2 henon_displacement = (henon_pos - centered * 3.0) * 0.02;

    // Logistic map
    float r = 3.8 + sin(t * 0.2) * 0.2;  // Chaos parameter
    float x = 0.5 + centered.x * 0.5;

    // Iterate logistic map
    for (int i = 0; i < 5; i++) {
        x = r * x * (1.0 - x);
    }

    vec2 logistic_displacement = vec2(
        (x - 0.5) * 0.1,
        sin(x * 6.28318 + t) * 0.05
    );

    // Duffing oscillator
    float alpha = -1.0;
    float beta = 1.0;
    float gamma = 0.3 + sin(t * 0.3) * 0.1;
    float omega = 1.0;

    float duffing_x = centered.x * 2.0;
    float duffing_y = centered.y * 2.0;

    // Duffing equation: x'' + δx' + αx + βx³ = γcos(ωt)
    float duffing_force = gamma * cos(omega * t) - 0.1 * duffing_y - alpha * duffing_x - beta * duffing_x * duffing_x * duffing_x;

    vec2 duffing_displacement = vec2(
        duffing_y * 0.01,
        duffing_force * 0.01
    );

    // Butterfly effect visualization
    float butterfly_sensitivity = 1000.0;
    float small_change = sin(t * 10.0 + length(centered) * butterfly_sensitivity) * 0.0001;

    vec2 butterfly_pos1 = centered;
    vec2 butterfly_pos2 = centered + vec2(small_change, 0.0);

    // Simple chaotic iteration
    for (int i = 0; i < 3; i++) {
        butterfly_pos1 = vec2(
            sin(butterfly_pos1.y + t * 0.5),
            cos(butterfly_pos1.x + t * 0.3)
        ) * 0.8;

        butterfly_pos2 = vec2(
            sin(butterfly_pos2.y + t * 0.5),
            cos(butterfly_pos2.x + t * 0.3)
        ) * 0.8;
    }

    vec2 butterfly_displacement = (butterfly_pos1 - butterfly_pos2) * 50.0;

    return henon_displacement + logistic_displacement + duffing_displacement + butterfly_displacement * 0.1;
}
