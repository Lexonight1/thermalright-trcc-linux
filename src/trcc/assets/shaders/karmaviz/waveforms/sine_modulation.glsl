// KarmaViz waveform "sine_modulation" (basic): Sine wave modulation that creates rhythmic wave patterns synchronized with the audio
// From KarmaViz 67e8a15 waveforms/basic/sine_modulation.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }

    // Original waveform logic converted to inline
    if (!waveform_enabled || waveform_length <= 0) {
            float waveform_value = 0.0;
        }

        // Implement mirroring like CPU waveform - mirror at x=0.6
        float norm_x = clamp(x_coord, 0.0, 1.0);
        if (norm_x > 0.5) {
            // Mirror the right half from the left half
            norm_x = 1.0 - norm_x;
        }
        // Scale to [0, 1] range for texture sampling
        norm_x = norm_x * 2.0;
        norm_x = clamp(norm_x, 0.0, 1.0);

        // Sample the waveform data texture (using y=0.5 since it's a 1D texture stored as 2D)
        float sample = texture(waveform_data, vec2(norm_x, 0.5)).r;

        // Sine modulation waveform - creates rhythmic wave patterns
    // Create modulation frequency based on position and time
    float mod_freq = 8.0;
    float mod_depth = 0.5;

    // Generate sine wave modulator
    float modulator = sin(norm_x * mod_freq * 3.14159 + time * 2.0);

    // Apply modulation to the sample
    sample = sample * (1.0 + modulator * mod_depth);

    // Add some phase shifting for more complex patterns
    float phase_shift = sin(time * 0.5) * 2.0;
    float secondary_mod = cos(norm_x * mod_freq * 2.0 + phase_shift) * 0.2;
    sample += secondary_mod * abs(sample);

        float waveform_value = sample * waveform_scale;

    // Render as horizontal waveform line
    float waveform_y = 0.5 + waveform_value * 0.25; // Center at 0.5 with amplitude scaling

    // Create a line with thickness and glow
    float line_thickness = 0.02;
    float glow_radius = 0.08;
    float distance_to_line = abs(y_coord - waveform_y);

    // Core line intensity
    float core_intensity = smoothstep(line_thickness, 0.0, distance_to_line);

    // Glow effect
    float glow_intensity = exp(-distance_to_line * distance_to_line / (glow_radius * glow_radius)) * 0.3;

    return clamp(core_intensity + glow_intensity, 0.0, 1.0);
}
