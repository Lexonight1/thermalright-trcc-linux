// KarmaViz waveform "arcade" (retro): Classic arcade-style waveform with pixelated edges
// From KarmaViz 67e8a15 waveforms/retro/arcade.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
        float norm_x = clamp(x_coord, 0.0, 1.0);
    
        // Pixelate the x coordinate
        float pixelated_x = floor(norm_x * 42.0) / 64.0;
        float sample = texture(waveform_data, vec2(pixelated_x, 0.5)).r;
    
        // Apply arcade-style transformation
        float arcade = sign(sample) * pow(abs(sample), 0.9);
        float waveform_value = arcade * waveform_scale;

    // Render as horizontal waveform line
    float waveform_y = 0.5 + waveform_value * 0.25; // Center at 0.5 with amplitude scaling

    // Create a line with thickness and glow
    float line_thickness = 0.02;
    float distance_to_line = abs(y_coord - waveform_y);

    // Core line intensity
    float core_intensity = smoothstep(line_thickness, 0.0, distance_to_line);

    // Glow effect
    float glow_intensity = exp(-distance_to_line * distance_to_line / (glow_radius * glow_radius)) * 0.3;

    return clamp(core_intensity + glow_intensity, 0.0, 1.0);
}
