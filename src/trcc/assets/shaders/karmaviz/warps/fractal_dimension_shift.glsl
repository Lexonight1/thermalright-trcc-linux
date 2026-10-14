// KarmaViz warp map "Fractal Dimension Shift" (experimental): Mandelbrot-like fractal iteration with dynamic dimensional scaling and escape velocity
// From KarmaViz 67e8a15 warp_maps/experimental/fractal_dimension_shift.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Fractal Dimension Shift
vec2 get_pattern(vec2 pos, float t) {
    vec2 z = (pos - 0.5) * 3.0;
    vec2 displacement = vec2(0.0);

    // Mandelbrot-like iteration with time evolution
    for (int i = 0; i < 8; i++) {
        float fi = float(i);

        // Complex number operations
        vec2 z_new = vec2(
            z.x * z.x - z.y * z.y + sin(t * 0.5 + fi * 0.3) * 0.3,
            2.0 * z.x * z.y + cos(t * 0.7 + fi * 0.2) * 0.3
        );

        // Escape velocity creates fractal boundaries
        float escape = length(z_new);
        if (escape > 2.0) break;

        // Accumulate displacement based on iteration
        displacement += z_new * exp(-fi * 0.5) * 0.02;
        z = z_new;
    }

    // Add dimensional folding
    float dimension = 2.0 + sin(t * 0.3) * 0.5; // 1.5 to 2.5 dimensions
    displacement *= pow(length(displacement) + 0.1, dimension - 2.0);

    return displacement;
}
