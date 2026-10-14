// KarmaViz warp map "Parametric Equations" (mathematical): Lissajous curves, cycloids, and epicycloids creating complex parametric motion patterns
// From KarmaViz 67e8a15 warp_maps/mathematical/parametric_equations.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Parametric Equations
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);
    float angle = atan(centered.y, centered.x);

    // Parametric curve parameters
    float param_t = radius * 5.0 + t * 0.8;

    // Lissajous curves (parametric)
    float a = 3.0 + sin(t * 0.2);  // X frequency
    float b = 2.0 + cos(t * 0.3);  // Y frequency
    float delta = t * 0.5;         // Phase difference

    vec2 lissajous = vec2(
        sin(a * param_t + delta),
        sin(b * param_t)
    ) * 0.03;

    // Cycloid curves
    float cycloid_r = 0.1 + sin(t * 0.4) * 0.05;
    vec2 cycloid = vec2(
        cycloid_r * (param_t - sin(param_t)),
        cycloid_r * (1.0 - cos(param_t))
    ) * 0.02;

    // Epicycloid
    float R = 0.2;  // Fixed circle radius
    float r = 0.05 + sin(t * 0.6) * 0.02;  // Rolling circle radius
    float ratio = (R + r) / r;
    vec2 epicycloid = vec2(
        ratio * cos(param_t) - cos(ratio * param_t),
        ratio * sin(param_t) - sin(ratio * param_t)
    ) * r * 0.5;

    // Combine parametric effects
    return lissajous + cycloid * 0.5 + epicycloid * 0.3;
}
