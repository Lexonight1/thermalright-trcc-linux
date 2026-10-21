// KarmaViz warp map "hologram_flicker" (futuristic): Holographic projection instability
// From KarmaViz 67e8a15 warp_maps/futuristic/hologram_flicker.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Hologram Flicker Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Hologram interference pattern
    float interference = sin(pos.x * 100.0 + t * 10.0) * cos(pos.y * 80.0 + t * 8.0);
    warp += vec2(interference, interference * 0.7) * 0.005;
    
    // Projection instability
    float instability = step(0.9, fract(sin(t * 5.0) * 43758.5453));
    if (instability > 0.5) {
        warp.x += sin(pos.y * 50.0) * 0.02;
        warp.y += cos(pos.x * 40.0) * 0.015;
    }
    
    // Depth layers
    float depth_shift = sin(t * 2.0) * 0.01;
    warp += vec2(depth_shift, depth_shift * 0.5);
    
    return warp;
}
