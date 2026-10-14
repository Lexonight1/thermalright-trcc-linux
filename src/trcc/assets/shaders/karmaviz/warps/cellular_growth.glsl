// KarmaViz warp map "Cellular Growth" (organic): Simulates organic cellular growth and division with pseudo-random cell expansion patterns
// From KarmaViz 67e8a15 warp_maps/organic/cellular_growth.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Cellular Growth
vec2 get_pattern(vec2 pos, float t) {
    vec2 cell_pos = pos * 8.0;
    vec2 cell_id = floor(cell_pos);
    vec2 local_pos = fract(cell_pos);

    // Generate pseudo-random growth for each cell
    float seed = sin(cell_id.x * 12.9898 + cell_id.y * 78.233) * 43758.5453;
    float growth_phase = fract(seed + t * 0.3);

    // Cell growth pattern
    float cell_size = smoothstep(0.0, 1.0, growth_phase) * 0.8;
    float dist_to_center = length(local_pos - 0.5);

    // Organic cell boundary
    float cell_boundary = cell_size + sin(atan(local_pos.y - 0.5, local_pos.x - 0.5) * 6.0 + t) * 0.1;

    if (dist_to_center < cell_boundary) {
        // Inside cell - create organic displacement
        vec2 cell_center = cell_id + 0.5;
        vec2 to_center = (cell_center - cell_pos) / 8.0;
        return to_center * 0.3 * growth_phase;
    }

    return vec2(0.0);
}
