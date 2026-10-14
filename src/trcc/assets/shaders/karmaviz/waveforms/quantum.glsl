// KarmaViz waveform "quantum" (advanced): Quantum-inspired waveform with probability waves, uncertainty effects, and particle-like behavior
// From KarmaViz 67e8a15 waveforms/mathematical/quantum.kvwf, MIT (c) 2025 Jason
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

        // Quantum waveform - simulates quantum mechanical wave-particle duality
    // Generate probability wave function
    float wave_number = 15.0;
    float probability_wave = sin(norm_x * wave_number + time * 2.0);

    // Apply quantum uncertainty principle - position vs momentum trade-off
    float uncertainty_x = norm_x;
    float uncertainty_p = cos(norm_x * wave_number * 2.0 + time);
    float uncertainty_factor = 1.0 / (1.0 + uncertainty_x * uncertainty_p * uncertainty_p);

    // Quantum tunneling effect - sample can 'tunnel' through barriers
    float barrier_height = 0.5;
    float tunnel_probability = exp(-abs(sample) / barrier_height);
    float tunneling = (fract(sin(norm_x * 43.758 + time) * 23.421) < tunnel_probability) ? 1.0 : 0.0;

    // Wave function collapse - discrete energy levels
    float energy_levels = 8.0;
    float quantized_sample = floor(sample * energy_levels) / energy_levels + sin(time);

    // Superposition of states
    float superposition = mix(sample, quantized_sample, 0.6);
    superposition *= (1.0 + probability_wave * 0.3);

    // Apply uncertainty and tunneling effects
    sample = superposition * uncertainty_factor;
    if (tunneling > 0.5) {
        sample *= 1.5;  // Amplify tunneled particles
    }

    // Add quantum interference patterns
    float interference = sin(norm_x * wave_number) * cos(norm_x * wave_number * 1.618 + time);
    sample += interference * abs(sample) * 0.2;

    // Heisenberg uncertainty - add random quantum fluctuations
    float quantum_noise = (fract(sin(norm_x * 12.9898 + time * 78.233) * 43758.5453) - 0.5) * 0.1;
    sample += quantum_noise * sqrt(abs(sample));

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
