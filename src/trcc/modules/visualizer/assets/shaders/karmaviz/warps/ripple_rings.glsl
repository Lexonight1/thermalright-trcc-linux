// KarmaViz warp map "Ripple Rings" (mathematical): Concentric ripple rings radiating from the center.
// From KarmaViz 67e8a15 warp_maps/mathematical/ripple_rings.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ripple Rings Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    float dist = length(pos - center);
    float ripple = sin(30.0 * dist - t * 3.0) * 0.015;
    return normalize(pos - center) * ripple;
}
