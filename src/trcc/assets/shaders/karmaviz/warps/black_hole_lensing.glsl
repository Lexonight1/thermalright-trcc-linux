// KarmaViz warp map "black_hole_lensing" (cosmic): Gravitational lensing around a black hole
// From KarmaViz 67e8a15 warp_maps/cosmic/black_hole_lensing.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Black Hole Lensing Warp Map
vec2 get_pattern(vec2 pos, float t) {
    vec2 center = vec2(0.5, 0.5);
    vec2 to_bh = pos - center;
    float dist = length(to_bh);
    
    // Schwarzschild radius
    float rs = 0.05;
    
    // Avoid singularity
    dist = max(dist, rs * 1.1);
    
    // Gravitational lensing
    float lensing_strength = rs / dist;
    
    // Light bending
    float bend_angle = lensing_strength * 2.0;
    
    // Rotate the direction vector
    float cos_bend = cos(bend_angle);
    float sin_bend = sin(bend_angle);
    
    vec2 bent_dir = vec2(
        to_bh.x * cos_bend - to_bh.y * sin_bend,
        to_bh.x * sin_bend + to_bh.y * cos_bend
    );
    
    // Frame dragging effect
    float drag_angle = lensing_strength * sin(t * 2.0) * 0.5;
    float cos_drag = cos(drag_angle);
    float sin_drag = sin(drag_angle);
    
    vec2 dragged = vec2(
        bent_dir.x * cos_drag - bent_dir.y * sin_drag,
        bent_dir.x * sin_drag + bent_dir.y * cos_drag
    );
    
    return (dragged - to_bh) * 0.3;
}
