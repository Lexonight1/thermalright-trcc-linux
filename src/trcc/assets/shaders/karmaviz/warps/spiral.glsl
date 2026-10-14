// KarmaViz warp map "Spiral" (basic): A classic spiral pattern that creates rotating distortions from the center
// From KarmaViz 67e8a15 warp_maps/basic/spiral.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Basic spiral warp pattern
vec2 get_pattern(vec2 pos, float t) {
    float angle = atan(pos.y - 0.5, pos.x - 0.5);
    float dist = length(pos - 0.5);
    return vec2(sin(angle * 3.0 + t + dist * 10.0),
                cos(angle * 2.0 + t + dist * 8.0)) * 0.03;
}
