// KarmaViz warp map "Plasma Tunnel" (experimental): A tunnel effect with plasma-like color modulation.
// From KarmaViz 67e8a15 warp_maps/experimental/plasma_tunnel.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Plasma Tunnel Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    float angle = atan(p.y, p.x) + sin(t + length(p) * 8.0) * 0.2;
    float radius = length(p) + 0.1 * sin(t * 2.0 + angle * 6.0);
    return vec2(cos(angle), sin(angle)) * radius * 0.04;
}
