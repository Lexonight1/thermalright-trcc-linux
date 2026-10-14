// KarmaViz warp map "Spiral Zoom" (experimental): Zooming spiral effect with time-based scaling.
// From KarmaViz 67e8a15 warp_maps/experimental/spiral_zoom.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Spiral Zoom Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    float angle = atan(p.y, p.x) + t * 0.7;
    float radius = length(p) * (1.0 + 0.3 * sin(t));
    float spiral = sin(10.0 * angle + radius * 8.0) * 0.025;
    return vec2(cos(angle), sin(angle)) * spiral;
}
