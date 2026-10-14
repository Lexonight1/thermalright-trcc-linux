// KarmaViz warp map "Plasma" (organic): Organic plasma-like distortions with flowing, liquid-like movement
// From KarmaViz 67e8a15 warp_maps/organic/plasma.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Organic plasma-like distortion
vec2 get_pattern(vec2 pos, float t) {
    float plasma1 = sin(pos.x * 16.0 + t) + sin(pos.y * 16.0 + t * 1.3);
    float plasma2 = sin((pos.x + pos.y) * 12.0 + t * 0.7) + sin((pos.x - pos.y) * 12.0 + t * 1.1);
    
    float offset_x = sin(plasma1 + plasma2 + t) * 0.03;
    float offset_y = cos(plasma1 - plasma2 + t * 1.2) * 0.03;
    
    return vec2(offset_x, offset_y);
}
