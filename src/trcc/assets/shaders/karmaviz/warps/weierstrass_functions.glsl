// KarmaViz warp map "Weierstrass Functions" (mathematical): Nowhere differentiable functions and elliptic function lattice structures
// From KarmaViz 67e8a15 warp_maps/mathematical/weierstrass_functions.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Weierstrass Elliptic and Nowhere Differentiable Functions
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Weierstrass nowhere differentiable function
    // W(x) = Σ a^n * cos(b^n * π * x) where 0 < a < 1, b odd, ab > 1 + 3π/2
    float a = 0.7;
    float b = 3.0;
    float weierstrass_x = 0.0;
    float weierstrass_y = 0.0;

    for (int n = 0; n < 8; n++) {
        float a_n = pow(a, float(n));
        float b_n = pow(b, float(n));

        weierstrass_x += a_n * cos(b_n * 3.14159 * (centered.x * 4.0 + t * 0.1));
        weierstrass_y += a_n * sin(b_n * 3.14159 * (centered.y * 4.0 + t * 0.1));
    }

    // Weierstrass elliptic function ℘(z)
    // Simplified approximation using lattice points
    vec2 z = centered * 3.0;
    vec2 wp = vec2(0.0); // Weierstrass P function

    // Sum over lattice points (simplified)
    for (int m = -2; m <= 2; m++) {
        for (int n = -2; n <= 2; n++) {
            if (m == 0 && n == 0) continue;

            vec2 lattice_point = vec2(float(m), float(n)) * 1.5;
            vec2 diff = z - lattice_point;
            float diff_mag_sq = dot(diff, diff);

            if (diff_mag_sq > 0.01) {
                wp += diff / (diff_mag_sq * diff_mag_sq);
            }
        }
    }

    vec2 displacement = vec2(
        weierstrass_x * 0.02 + wp.x * 0.005,
        weierstrass_y * 0.02 + wp.y * 0.005
    );

    // Add fractal self-similarity
    float scale = 1.0;
    for (int i = 0; i < 3; i++) {
        scale *= 0.5;
        displacement += vec2(
            sin(centered.x / scale + t * (1.0 + float(i))) * scale * 0.01,
            cos(centered.y / scale + t * (1.0 + float(i))) * scale * 0.01
        );
    }

    return displacement;
}
