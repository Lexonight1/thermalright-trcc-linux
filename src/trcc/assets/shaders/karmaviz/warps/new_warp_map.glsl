// KarmaViz warp map "New Warp Map" (custom): A new custom warp map
// From KarmaViz 67e8a15 warp_maps/custom/new_warp_map.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Animated wave warp map template
vec2 get_pattern(vec2 pos, float t) {
    // pos: current pixel position (0.0 to 1.0)
    // t: time variable for animation

    // Center the coordinates around (0.5, 0.5)
    vec2 centered = pos - 0.5;

    // Create animated wave distortion
    float wave_freq = 8.0;
    float wave_speed = 2.0;
    float wave_amplitude = 0.05;

    // Horizontal waves based on Y position
    float wave_x = sin(tan(pos.y *2) * wave_freq + t * wave_speed) * wave_amplitude;

    // Vertical waves based on X position
    float wave_y = cos(pos.x * wave_freq + t * wave_speed) * wave_amplitude;

    // Add some circular distortion for more interesting effects
    float dist = length(centered);
    float radial_wave = sin(tan(wave_x * 3.0) + t * 3.0) * 0.02;

    return vec2(wave_x, wave_y);
}
