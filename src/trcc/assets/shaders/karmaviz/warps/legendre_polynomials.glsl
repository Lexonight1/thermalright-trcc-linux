// KarmaViz warp map "Legendre Polynomials" (mathematical): Orthogonal polynomials and spherical harmonics with angular momentum visualization
// From KarmaViz 67e8a15 warp_maps/mathematical/legendre_polynomials.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Legendre Polynomials and Spherical Harmonics
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float x = centered.x * 2.0; // Map to [-1, 1]
    float theta = atan(centered.y, centered.x);

    // Legendre polynomials P_n(x)
    float P0 = 1.0;
    float P1 = x;
    float P2 = 0.5 * (3.0 * x * x - 1.0);
    float P3 = 0.5 * (5.0 * x * x * x - 3.0 * x);
    float P4 = 0.125 * (35.0 * x * x * x * x - 30.0 * x * x + 3.0);

    // Time-varying combination
    float legendre_sum = P0 * sin(t * 0.5) +
                        P1 * cos(t * 0.7) +
                        P2 * sin(t * 0.9) +
                        P3 * cos(t * 1.1) +
                        P4 * sin(t * 1.3);

    // Spherical harmonics approximation
    float Y_real = legendre_sum * cos(theta * 3.0 + t);
    float Y_imag = legendre_sum * sin(theta * 3.0 + t);

    vec2 displacement = vec2(
        Y_real * cos(theta + t * 0.5),
        Y_imag * sin(theta + t * 0.5)
    ) * 0.03;

    // Add orthogonality visualization
    float orthogonal_effect = 0.0;
    for (int n = 0; n < 4; n++) {
        float weight = sin(t * 0.2 + float(n));
        orthogonal_effect += weight * cos(float(n + 1) * acos(clamp(x, -1.0, 1.0)));
    }

    displacement += vec2(
        orthogonal_effect * 0.01,
        sin(orthogonal_effect + t * 2.0) * 0.01
    );

    return displacement;
}
