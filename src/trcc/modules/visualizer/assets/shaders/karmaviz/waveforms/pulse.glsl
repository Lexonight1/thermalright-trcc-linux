// KarmaViz waveform "pulse" (advanced): Pulsing waveform that creates rhythmic beats with amplitude-based intensity variations
// From KarmaViz 67e8a15 waveforms/basic/pulse.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }

    // Original waveform logic converted to inline
    if (!waveform_enabled || waveform_length <= 0) {
            float waveform_value = 0.0;
        }

        // Implement mirroring like CPU waveform - mirror at x=0.5
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

        // Pulse waveform - creates rhythmic pulsing effects
    // Create pulse frequency based on audio amplitude
    float pulse_freq = 8.0 + abs(sample) * 20.0;

    // Generate pulse wave
    float pulse = sin(norm_x * pulse_freq * 3.14159 + time * 4.0);
    pulse = pulse > 0.0 ? 1.0 : -0.3;  // Square wave with asymmetric amplitude

    // Apply pulse to sample with amplitude modulation
    float pulse_intensity = 0.3 + abs(sample) * 0.7;
    sample = sample * (1.0 + pulse * pulse_intensity);

    // Add some harmonic distortion for more interesting pulses
    float harmonic = sin(norm_x * pulse_freq * 2.0 + time * 3.0) * 0.1;
    sample += harmonic * abs(sample);

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
