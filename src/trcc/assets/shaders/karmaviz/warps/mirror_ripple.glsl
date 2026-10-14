// KarmaViz warp map "Mirror Ripple" (experimental): Mirrored ripple effect from both sides.
// From KarmaViz 67e8a15 warp_maps/experimental/mirror_ripple.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Mirror Ripple Warp Map
vec2 get_pattern(vec2 pos, float t) {
    float rippleL = sin(250.0 * pos.x - t * 2.0) * 0.012;
    float rippleR = sin(25.0 * (1.0 - pos.x) - t * 2.0) * 0.012;
    return vec2(rippleL + rippleR, 0.0);
}
