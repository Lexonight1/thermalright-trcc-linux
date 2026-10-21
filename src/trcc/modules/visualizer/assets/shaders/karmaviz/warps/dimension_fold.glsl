// KarmaViz warp map "dimension_fold" (experimental): Higher dimensional space folding
// From KarmaViz 67e8a15 warp_maps/experimental/dimension_fold.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Dimension Fold Warp Map
vec2 get_pattern(vec2 pos, float t) {
    // Project 3D folding onto 2D
    float z = sin(pos.x * 6.28318) * cos(pos.y * 6.28318);
    
    // Fold along multiple axes
    vec3 fold_point = vec3(pos.x, pos.y, z);
    
    // 4D rotation matrix projection
    float angle = t * 0.5;
    float cos_a = cos(angle);
    float sin_a = sin(angle);
    
    // Rotate in 4D space (simplified)
    vec2 rotated = vec2(
        fold_point.x * cos_a - fold_point.z * sin_a,
        fold_point.y * cos_a + fold_point.z * sin_a * 0.5
    );
    
    // Fold back to 2D
    vec2 folded = rotated - pos;
    
    return folded * 0.1;
}
