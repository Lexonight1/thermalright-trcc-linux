// KarmaViz warp map "vhs_tracking" (retro): VHS tape tracking distortion
// From KarmaViz 67e8a15 warp_maps/retro/vhs_tracking.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// VHS Tracking Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Horizontal tracking errors
    float tracking_error = sin(pos.y * 100.0 + t * 0.1) * 0.01;
    warp.x = tracking_error;
    
    // Vertical sync issues
    float vsync_glitch = step(0.98, fract(sin(t * 0.5) * 43758.5453));
    if (vsync_glitch > 0.5) {
        warp.y = sin(pos.x * 50.0) * 0.02;
    }
    
    // Tape stretch
    float stretch_zone = smoothstep(0.3, 0.7, pos.y);
    warp.x += sin(pos.x * 20.0 + t * 2.0) * stretch_zone * 0.005;
    
    // Dropout areas
    float dropout = step(0.95, fract(sin(pos.y * 50.0 + t * 0.2) * 43758.5453));
    warp *= (1.0 - dropout * 0.8);
    
    return warp;
}
