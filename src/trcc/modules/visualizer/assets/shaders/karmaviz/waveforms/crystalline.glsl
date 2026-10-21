// KarmaViz waveform "crystalline" (advanced): Crystal-like waveform with sharp geometric patterns and prismatic reflections
// From KarmaViz 67e8a15 waveforms/advanced/crystalline.kvwf, MIT (c) 2025 Jason
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

        // Crystalline waveform - creates sharp, geometric crystal-like patterns
        // Create angular, faceted transformations
        float crystal_x = norm_x * 12.0;
        float facet = floor(crystal_x) / 12.0;

        // Generate sharp angular waves
        float angle1 = abs(fract(crystal_x) - 0.5) * 2.0;  // Triangle wave
        float angle2 = abs(fract(crystal_x * 2.0 + 0.5) - 0.5) * 2.0;  // Offset triangle

        // Combine angles for complex faceting
        float crystal_pattern = (angle1 + angle2 * 0.5) / 1.5;

        // Apply crystalline transformation to sample
        sample = sample * (0.7 + crystal_pattern * 0.6);

        // Add prismatic reflections with time-based shifting
        float prism_shift = sin(time * 0.8) * 0.3;
        float reflection1 = abs(fract(norm_x * 8.0 + prism_shift) - 0.5) * 2.0;
        float reflection2 = abs(fract(norm_x * 16.0 - prism_shift) - 0.5) * 2.0;

        // Blend reflections with original sample
        sample += (reflection1 - reflection2) * abs(sample) * 0.2;

        // Add sharp harmonic overtones for crystal clarity
        float harmonic = sign(sin(norm_x * 24.0 + time * 2.0)) * 0.1;
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
