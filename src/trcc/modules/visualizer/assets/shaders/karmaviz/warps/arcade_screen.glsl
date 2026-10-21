// KarmaViz warp map "arcade_screen" (retro): Classic arcade screen distortion
// From KarmaViz 67e8a15 warp_maps/retro/arcade_screen.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Arcade Screen Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Scanline wobble
    float scanline_wobble = sin(pos.y * 200.0 + t * 5.0) * 0.002;
    warp.x = scanline_wobble;
    
    // Screen bulge (old CRT effect)
    vec2 center = vec2(0.5, 0.5);
    vec2 offset = pos - center;
    float bulge = length(offset) * 0.02;
    warp += offset * bulge;
    
    // Phosphor persistence
    float phosphor = sin(pos.x * 300.0) * sin(pos.y * 200.0) * 0.001;
    warp += vec2(phosphor, phosphor * 0.5);
    
    return warp;
}
