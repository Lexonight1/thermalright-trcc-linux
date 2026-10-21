// KarmaViz warp map "Modular Forms" (mathematical): Eisenstein series and j-invariant with fundamental domain transformations
// From KarmaViz 67e8a15 warp_maps/mathematical/modular_forms.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Modular Forms and Eisenstein Series
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Map to upper half-plane (Im(z) > 0)
    vec2 z = vec2(centered.x * 4.0, abs(centered.y) * 2.0 + 0.1);

    // Eisenstein series E_4(τ) approximation
    vec2 eisenstein_4 = vec2(1.0, 0.0);

    // Sum over lattice points (m,n) ≠ (0,0)
    for (int m = -3; m <= 3; m++) {
        for (int n = -3; n <= 3; n++) {
            if (m == 0 && n == 0) continue;

            // τ = z, lattice point mτ + n
            vec2 lattice_tau = vec2(float(m) * z.x + float(n), float(m) * z.y);
            float lattice_norm = dot(lattice_tau, lattice_tau);

            if (lattice_norm > 0.01) {
                // 1/(mτ + n)^4 contribution
                float inv_norm_4 = 1.0 / (lattice_norm * lattice_norm);
                eisenstein_4 += vec2(inv_norm_4, 0.0);
            }
        }
    }

    // Modular transformation τ → -1/τ
    vec2 z_inv = vec2(z.x, -z.y) / dot(z, z);

    // j-invariant approximation
    float j_real = eisenstein_4.x * eisenstein_4.x * eisenstein_4.x;

    vec2 displacement = vec2(
        sin(j_real * 0.1 + t) * 0.02,
        cos(eisenstein_4.x + t * 1.5) * 0.02
    );

    // Add fundamental domain visualization
    float fundamental_domain = 0.0;
    if (abs(z.x) <= 0.5 && dot(z, z) >= 1.0) {
        fundamental_domain = 1.0;
    }

    displacement += vec2(
        fundamental_domain * sin(t * 3.0) * 0.01,
        cos(length(z) + t * 2.0) * 0.01
    );

    return displacement;
}
