// KarmaViz warp map "Be Still My Heart" (custom): EKG-like waveforms
// From KarmaViz 67e8a15 warp_maps/custom/be_still_my_heart.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Animated wave warp map template
vec2 get_pattern(vec2 pos, float t) {
    // pos: current pixel position (0.0 to 1.0)
    // t: time variable for animation

    // Center the coordinates around (0.5, 0.5)
    vec2 centered = pos - 0.5;

    // Create animated wave distortion
    float wave_freq = 5.0;
    float wave_speed = 1.0;
    float wave_amplitude = 0.2;

    // Horizontal waves based on Y position
    float wave_x = sin(pos.y * wave_freq + t * wave_speed) * wave_amplitude;

    float wave_y = cos(3.14) * tan(pos.x * wave_freq - t * wave_speed) * wave_amplitude;

    float dist = length(centered - wave_amplitude);
    float radial_wave = sin(dist * 1.0 + t * 3.0) * 0.02;

    return vec2(wave_y + radial_wave, wave_y / t * radial_wave);
}
