// KarmaViz warp map "Muscle Fiber" (organic): Biological muscle fiber contraction with longitudinal waves and transverse bulging
// From KarmaViz 67e8a15 warp_maps/organic/muscle_fiber.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Muscle Fiber
vec2 get_pattern(vec2 pos, float t) {
    // Create parallel muscle fibers
    float fiber_spacing = 0.08;
    float fiber_id = floor(pos.y / fiber_spacing);
    float fiber_local = fract(pos.y / fiber_spacing);

    // Fiber contraction wave
    float contraction_wave = sin(pos.x * 8.0 - t * 4.0 + fiber_id * 0.5);
    float contraction_strength = 0.5 + contraction_wave * 0.3;

    // Fiber thickness variation
    float fiber_thickness = 0.3 + sin(fiber_id * 2.0 + t * 1.5) * 0.1;

    vec2 displacement = vec2(0.0);

    // Inside fiber
    if (abs(fiber_local - 0.5) < fiber_thickness) {
        // Longitudinal contraction
        displacement.x = contraction_wave * 0.02;

        // Transverse bulging
        float bulge = sin(pos.x * 12.0 - t * 3.0) * 0.01;
        displacement.y = bulge * contraction_strength;
    }

    // Add overall muscle tension
    displacement += vec2(sin(t * 2.0) * 0.005, 0.0);

    return displacement;
}
