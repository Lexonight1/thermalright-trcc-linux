// KarmaViz warp map "Number Theory" (mathematical): Prime spirals, Fibonacci sequences, continued fractions, and modular arithmetic patterns
// From KarmaViz 67e8a15 warp_maps/mathematical/number_theory.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Number Theory Visualizations
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Prime number spiral (Ulam spiral)
    vec2 grid_pos = floor(pos * 20.0);
    float n = grid_pos.x + grid_pos.y * 20.0 + t * 2.0;

    // Simple primality test approximation for visualization
    bool is_prime = true;
    if (n < 2.0) is_prime = true;
    for (float i = 2.0; i * i <= n && i < 10.0; i += 1.0) {
        if (mod(n, i) < 0.1) {
            is_prime = false;
            break;
        }
    }

    vec2 prime_displacement = vec2(0.0);
    if (is_prime) {
        prime_displacement = normalize(centered) * 0.02 * sin(n + t);
    }

    // Fibonacci spiral
    float phi = 1.618033988749;  // Golden ratio
    float fib_angle = length(centered) * phi * 2.0 * 3.14159 + t * 0.5;
    float fib_radius = sqrt(length(centered)) * 0.1;

    vec2 fibonacci_displacement = vec2(
        cos(fib_angle) * fib_radius,
        sin(fib_angle) * fib_radius
    ) * 0.03;

    // Continued fractions visualization
    float x = pos.x * 10.0 + t * 0.3;
    float cf_value = x;
    vec2 cf_displacement = vec2(0.0);

    // Approximate continued fraction expansion
    for (int i = 0; i < 5; i++) {
        float integer_part = floor(cf_value);
        float fractional_part = cf_value - integer_part;

        if (fractional_part > 0.01) {
            cf_value = 1.0 / fractional_part;
            cf_displacement += vec2(
                sin(integer_part + t),
                cos(integer_part + t)
            ) * 0.005;
        } else {
            break;
        }
    }

    // Modular arithmetic patterns
    float mod_base = 7.0 + sin(t * 0.2) * 2.0;
    float mod_pattern = mod(pos.x * pos.y * 100.0 + t * 10.0, mod_base);
    vec2 modular_displacement = vec2(
        sin(mod_pattern * 2.0 * 3.14159 / mod_base),
        cos(mod_pattern * 2.0 * 3.14159 / mod_base)
    ) * 0.01;

    return prime_displacement + fibonacci_displacement + cf_displacement + modular_displacement;
}
