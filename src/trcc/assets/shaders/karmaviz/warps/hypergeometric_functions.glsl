// KarmaViz warp map "Hypergeometric Functions" (mathematical): Generalized hypergeometric series with confluent cases and special function relationships
// From KarmaViz 67e8a15 warp_maps/mathematical/hypergeometric_functions.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Hypergeometric Functions and Special Cases
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float z = length(centered) * 2.0;
    float arg = atan(centered.y, centered.x);

    // Hypergeometric function 2F1(a,b;c;z) series approximation
    float a = 0.5 + sin(t * 0.1) * 0.3;
    float b = 1.0 + cos(t * 0.15) * 0.5;
    float c = 2.0;

    float hypergeometric = 1.0; // First term
    float term = 1.0;

    // Series expansion (first few terms)
    for (int n = 1; n <= 8; n++) {
        float n_float = float(n);
        term *= (a + n_float - 1.0) * (b + n_float - 1.0) * z / ((c + n_float - 1.0) * n_float);
        hypergeometric += term;

        if (abs(term) < 0.001) break; // Convergence check
    }

    // Confluent hypergeometric function 1F1(a;c;z)
    float confluent = 1.0;
    float confluent_term = 1.0;

    for (int n = 1; n <= 6; n++) {
        float n_float = float(n);
        confluent_term *= (a + n_float - 1.0) * z / ((c + n_float - 1.0) * n_float);
        confluent += confluent_term;
    }

    // Create displacement using hypergeometric functions
    vec2 displacement = vec2(
        hypergeometric * cos(arg * 3.0 + t) * 0.02,
        confluent * sin(arg * 5.0 + t * 1.2) * 0.02
    );

    // Add special cases (Bessel, error functions, etc.)
    float special_case = exp(-z * z) * cos(2.0 * z + t); // Gaussian-related

    displacement += vec2(
        special_case * sin(arg + t * 0.8) * 0.01,
        cos(special_case + t * 2.5) * 0.01
    );

    return displacement;
}
