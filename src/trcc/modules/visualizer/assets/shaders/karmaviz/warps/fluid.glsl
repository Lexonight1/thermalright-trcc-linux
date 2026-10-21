// KarmaViz warp map "fluid" (organic): Organic fluid-like distortions that flow and ripple naturally
// From KarmaViz 67e8a15 warp_maps/organic/fluid.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Fluid-like organic distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = pos - 0.5;
    float diagonal = sin((pos.x + pos.y) * 25.0 + t) * cos((pos.x - pos.y) * 20.0 - t);
    float grid = sin(pos.x * 20.0) * sin(pos.y * 20.0);
    return center * (diagonal * grid) * 0.05;
}
