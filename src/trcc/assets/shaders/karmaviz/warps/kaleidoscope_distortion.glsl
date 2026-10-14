// KarmaViz warp map "kaleidoscope_distortion" (geometric): Kaleidoscope effect with variable segments and radial distortion
// From KarmaViz 67e8a15 warp_maps/geometric/kaleidoscope_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Kaleidoscope pattern with radial symmetry
vec2 get_pattern(vec2 pos, float t) {
    t = time / 100;
    vec2 center = pos - sin(t * .00001) - 0.5;
    float angle = atan(center.y, center.x) * 6.0;
    float dist = length(center);
    return vec2(sin(angle - t /20) * dist,
                atan(angle - t) * dist) * .1;
}
