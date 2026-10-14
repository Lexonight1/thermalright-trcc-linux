// KarmaViz warp map "Wonder Wall" (custom): It's an Oasis.. or a mirage...
// From KarmaViz 67e8a15 warp_maps/custom/wonder_wall.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Ripple Rings Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    float dist = length(pos - center);
    float ripple = sin(66.0 * dist - t * 3.0) * 0.110;
    return tan(10 / pos.x + t - center) * ripple * pos.y;
}
