// KarmaViz warp map "Temporal Paradox" (experimental): Multiple time streams with past, present, and future influences creating temporal interference
// From KarmaViz 67e8a15 warp_maps/experimental/temporal_paradox.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Temporal Paradox Effect
vec2 get_pattern(vec2 pos, float t) {
    // Multiple time streams
    float t1 = t * 1.0;           // Present
    float t2 = t * 0.5 + 10.0;    // Past (slower, offset)
   float t3 = t * 2.0 - 5.0;     // Future (faster, offset)z
    // Past influence (fading memories)
    vec2 past_warp = vec2(
        sin(pos.x * 8.0 + t2) * exp(-t2 *length(t2)),
        atan(pos.y * 6.0 + t2) * exp(-t2 * 0.1)
    ) * 0.03;

    // Present reality (strongest influence)
    vec2 present_warp = vec2(
        sin(pos.x * 12.0 + pos.y * 10.0 + t1),
        cos(pos.x * 10.0 - pos.y * 8.0 + t1)
    ) * 0.05;

    // Future echoes (uncertain, flickering)
    float future_uncertainty = sin(t3 * 5.0) * 0.5 + 0.5;
    vec2 future_warp = vec2(
        cos(pos.x * 15.0 + t3) * future_uncertainty,
       cos(pos.y * 12.0 + t3) * future_uncertainty
    ) * 0.02;

    // Temporal interference zones
    float interference = sin(pos.x * 20.0 + t1) * cos(pos.y * 18.0 + t2) * sin(t3);
    vec2 paradox_zone = vec2(interference) * 0.01;

    // Causality breakdown near center
    vec2 centered = pos - 0.5;
    float causality = 1.0 - exp(-length(centered) * 4.0);

    return (past_warp + present_warp + future_warp + paradox_zone) * causality;
}
