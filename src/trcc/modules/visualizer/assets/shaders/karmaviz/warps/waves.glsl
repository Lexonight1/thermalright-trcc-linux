// KarmaViz warp map "Waves" (basic): Simple sine and cosine waves creating horizontal and vertical distortions
// From KarmaViz 67e8a15 warp_maps/basic/waves.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Basic wave pattern
vec2 get_pattern(vec2 pos, float t) {
    return vec2(sin(pos.x * 30.0 + t) * 0.01,
                cos(pos.y * 20.0 - t * 2.0) * 0.03 - 0.02);
}
