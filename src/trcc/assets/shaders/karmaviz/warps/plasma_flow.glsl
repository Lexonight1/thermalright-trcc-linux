// KarmaViz warp map "Plasma Flow" (organic): Creates flowing plasma-like distortions with multiple frequency layers
// From KarmaViz 67e8a15 warp_maps/organic/plasma_flow.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Multiple frequency layers for complex plasma effect
    float x = pos.x;
    float y = pos.y;
    
    // Layer 1: Large slow waves
    float wave1_x = sin(x * 3.0 + t * 0.8) * cos(y * 2.0 + t * 0.6);
    float wave1_y = cos(x * 2.5 + t * 0.7) * sin(y * 3.5 + t * 0.9);
    
    // Layer 2: Medium frequency
    float wave2_x = sin(x * 8.0 + t * 1.5) * cos(y * 6.0 + t * 1.2);
    float wave2_y = cos(x * 7.0 + t * 1.3) * sin(y * 9.0 + t * 1.6);
    
    // Layer 3: High frequency detail
    float wave3_x = sin(x * 15.0 + t * 2.5) * cos(y * 12.0 + t * 2.2);
    float wave3_y = cos(x * 13.0 + t * 2.3) * sin(y * 16.0 + t * 2.7);
    
    // Combine layers with different weights
    vec2 flow = vec2(
        wave1_x * 0.4 + wave2_x * 0.3 + wave3_x * 0.2,
        wave1_y * 0.4 + wave2_y * 0.3 + wave3_y * 0.2
    );
    
    return flow * 0.025;
}
