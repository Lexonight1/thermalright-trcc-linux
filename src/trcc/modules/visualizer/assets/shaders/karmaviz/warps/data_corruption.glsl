// KarmaViz warp map "data_corruption" (digital): Digital data corruption visualization
// From KarmaViz 67e8a15 warp_maps/digital/data_corruption.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Data Corruption Warp Map
vec2 get_pattern(vec2 pos, float t) {
    // Scanline effect
    float scanline = floor(pos.y * 100.0);
    float corruption_chance = fract(sin(scanline * 12.9898 + t * 0.1) * 43758.5453);
    
    vec2 warp = vec2(0.0);
    
    if (corruption_chance > 0.98) {
        // Horizontal displacement corruption
        float displacement = sin(scanline * 0.1 + t * 5.0) * 0.1;
        warp.x = displacement;
    }
    
    if (corruption_chance > 0.995) {
        // Vertical jitter
        warp.y = (fract(sin(pos.x * 1000.0 + t * 10.0) * 43758.5453) - 0.5) * 0.05;
    }
    
    return warp;
}
