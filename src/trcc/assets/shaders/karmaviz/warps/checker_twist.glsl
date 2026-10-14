// KarmaViz warp map "Checker Twist" (Mathematical): Checkerboard pattern with a twist effect.
// From KarmaViz 67e8a15 warp_maps/mathematical/checker_twist.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Checker Twist Warp Map
vec2 get_pattern(vec2 pos, float t) {
    float checker = step(0.5, mod(floor(pos.x * 10.0) + floor(pos.y * 10.0), 2.0));
    float twist = sin((pos.x + pos.y) * 10.0 + t * 2.0) * 0.02;
    return vec2(twist * checker, -twist * (1.0 - checker));
}
