// KarmaViz warp map "pixel_shift" (digital): Digital pixel shifting and glitch effects
// From KarmaViz 67e8a15 warp_maps/digital/pixel_shift.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Pixel Shift Warp Map
vec2 get_pattern(vec2 pos, float t) {
    // Quantize position to create pixel blocks
    vec2 pixelated = floor(pos * 32.0) / 32.0;
    
    // Random shift per pixel block
    float random_x = fract(sin(pixelated.x * 12.9898 + pixelated.y * 78.233) * 43758.5453);
    float random_y = fract(sin(pixelated.x * 78.233 + pixelated.y * 12.9898) * 43758.5453);
    
    // Time-based glitch intensity
    float glitch_intensity = step(0.95, fract(sin(t * 10.0) * 43758.5453)) * 0.02;
    
    return vec2(random_x - 0.5, random_y - 0.5) * glitch_intensity;
}
