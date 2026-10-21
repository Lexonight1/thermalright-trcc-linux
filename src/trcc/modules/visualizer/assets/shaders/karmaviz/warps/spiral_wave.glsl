// KarmaViz warp map "Spiral Wave" (mathematical): Spiral pattern using polar coordinates and sine modulation.
// From KarmaViz 67e8a15 warp_maps/mathematical/spiral_wave.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Spiral Wave Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    float angle = atan(p.y, p.x);
    float radius = length(p);
    float spiral = sin(8.0 * angle + t * 2.0 + radius * 20.0) * 0.03;
    return vec2(cos(angle), sin(angle)) * spiral;
}
