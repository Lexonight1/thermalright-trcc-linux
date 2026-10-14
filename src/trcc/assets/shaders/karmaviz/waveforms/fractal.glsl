// KarmaViz waveform "fractal" (advanced): Fractal-inspired waveform with self-similar patterns and recursive transformations
// From KarmaViz 67e8a15 waveforms/advanced/fractal.kvwf, MIT (c) 2025 Jason
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

        // Fractal waveform - creates self-similar recursive patterns
    // Generate multiple octaves of noise for fractal effect
    float fractal_sample = sample;

    // First octave - base frequency
    float octave1 = sin(norm_x * 16.0 + time) * 0.5;
    fractal_sample += octave1 * abs(sample) * 0.3;

    // Second octave - double frequency, half amplitude
    float octave2 = sin(norm_x * 32.0 + time * 1.5) * 0.25;
    fractal_sample += octave2 * abs(sample) * 0.2;

    // Third octave - quadruple frequency, quarter amplitude
    float octave3 = sin(norm_x * 64.0 + time * 2.0) * 0.125;
    fractal_sample += octave3 * abs(sample) * 0.1;

    // Fourth octave - very high frequency detail
    float octave4 = sin(norm_x * 128.0 + time * 2.5) * 0.0625;
    fractal_sample += octave4 * abs(sample) * 0.05;

    // Apply fractal scaling
    sample = fractal_sample;

    // Add some chaotic behavior based on sample position
    float chaos = fract(sin(norm_x * 12.9898 + time) * 43758.5453);
    sample += (chaos - 0.5) * abs(sample) * 0.1;

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
