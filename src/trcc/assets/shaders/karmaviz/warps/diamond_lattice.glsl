// KarmaViz warp map "Diamond Lattice" (geometric): Creates a diamond-shaped lattice distortion
// From KarmaViz 67e8a15 warp_maps/geometric/diamond_lattice.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Diamond lattice transformation
    vec2 diamond_pos = pos * 6.0;
    
    // Rotate 45 degrees to create diamond shape
    float cos45 = 0.707107;
    float sin45 = 0.707107;
    vec2 rotated = vec2(
        diamond_pos.x * cos45 - diamond_pos.y * sin45,
        diamond_pos.x * sin45 + diamond_pos.y * cos45
    );
    
    // Create lattice effect
    vec2 lattice = fract(rotated) - 0.5;
    float dist = max(abs(lattice.x), abs(lattice.y));
    
    // Animate the effect
    float wave = sin(t * 3.0 + dist * 8.0);
    
    return lattice * wave * 0.015;
}
