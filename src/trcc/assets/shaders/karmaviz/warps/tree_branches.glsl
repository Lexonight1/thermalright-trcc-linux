// KarmaViz warp map "tree_branches" (organic): Organic tree branch growth patterns
// From KarmaViz 67e8a15 warp_maps/organic/tree_branches.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Tree Branches Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 warp = vec2(0.0);
    
    // Main trunk
    float trunk_influence = exp(-abs(pos.x - 0.5) * 8.0);
    warp.y += sin(pos.y * 10.0 + t * 0.5) * trunk_influence * 0.01;
    
    // Branch system
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        float branch_y = 0.2 + fi * 0.15;
        float branch_angle = sin(fi * 2.1 + t * 0.2) * 0.8;
        
        // Branch influence
        vec2 branch_start = vec2(0.5, branch_y);
        vec2 branch_dir = vec2(sin(branch_angle), cos(branch_angle));
        
        // Distance to branch line
        vec2 to_point = pos - branch_start;
        float branch_t = clamp(dot(to_point, branch_dir), 0.0, 0.3);
        vec2 branch_point = branch_start + branch_dir * branch_t;
        float branch_dist = distance(pos, branch_point);
        
        if (branch_dist < 0.1) {
            float branch_strength = (0.1 - branch_dist) / 0.1;
            warp += branch_dir * branch_strength * 0.02 * sin(t * 1.0 + fi);
        }
    }
    
    return warp;
}
