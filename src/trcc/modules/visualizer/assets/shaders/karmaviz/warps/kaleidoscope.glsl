// KarmaViz warp map "kaleidoscope" (geometric): Creates radial symmetry patterns like looking through a kaleidoscope
// From KarmaViz 67e8a15 warp_maps/geometric/kaleidoscope.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Kaleidoscope pattern with radial symmetry
vec2 get_pattern(vec2 pos, float t) {
    t = time / 5;
    vec2 center = pos - 0.5;
    float angle = atan(center.y, center.x) * 6.0;
    float dist = length(center);
    return vec2(sin(angle + t) * dist,
                cos(angle - t) * dist) * .1;
}
