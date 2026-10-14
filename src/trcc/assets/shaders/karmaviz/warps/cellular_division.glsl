// KarmaViz warp map "cellular_division" (organic): Biological cell division patterns
// From KarmaViz 67e8a15 warp_maps/organic/cellular_division.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Cellular Division Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Cell centers
    for (int i = 0; i < 8; i++) {
        float fi = float(i);
        vec2 cell_center = vec2(
            0.2 + 0.6 * fract(sin(fi * 12.9898) * 43758.5453),
            0.2 + 0.6 * fract(sin(fi * 78.233) * 43758.5453)
        );
        
        float dist = distance(pos, cell_center);
        float cell_phase = t * 0.5 + fi * 0.8;
        
        // Cell growth and division
        float cell_size = 0.08 + 0.04 * sin(cell_phase);
        
        if (dist < cell_size) {
            // Inside cell - push outward during division
            float division_strength = sin(cell_phase * 2.0) * 0.5 + 0.5;
            vec2 push_dir = normalize(pos - cell_center);
            warp += push_dir * division_strength * 0.02;
        }
        
        // Cell membrane tension
        if (dist > cell_size && dist < cell_size + 0.02) {
            float membrane_pull = exp(-(dist - cell_size) * 50.0);
            vec2 pull_dir = normalize(cell_center - pos);
            warp += pull_dir * membrane_pull * 0.01;
        }
    }
    
    return warp;
}
