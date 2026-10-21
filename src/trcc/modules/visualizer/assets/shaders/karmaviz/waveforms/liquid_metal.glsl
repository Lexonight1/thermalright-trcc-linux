// KarmaViz waveform "liquid_metal" (advanced): Fluid metallic waveform that flows and morphs like liquid mercury with surface tension effects and metallic reflections
// From KarmaViz 67e8a15 waveforms/motion/liquid_metal.kvwf, MIT (c) 2025 Jason
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

        // Liquid Metal - creates flowing metallic effects with surface tension and viscosity
    // Sample FFT for dynamic viscosity control
    float fft_bass = texture(fft_data, vec2(0.05, 0.5)).r;
    float fft_treble = texture(fft_data, vec2(0.8, 0.5)).r;

    // Create base fluid flow with multiple frequencies
    float flow_speed = 1.5 + fft_bass * 3.0;
    float viscosity = 0.3 + fft_treble * 0.7;

    // Generate fluid motion with Perlin-like noise
    float flow1 = sin(norm_x * 4.0 + time * flow_speed);
    float flow2 = sin(norm_x * 7.0 + time * flow_speed * 0.7 + 1.2);
    float flow3 = sin(norm_x * 11.0 + time * flow_speed * 0.5 + 2.4);

    // Combine flows for complex fluid motion
    float fluid_motion = (flow1 + flow2 * 0.6 + flow3 * 0.4) / 2.0;

    // Apply surface tension effects - smooth out sharp edges
    float surface_tension = 0.2 + viscosity * 0.3;
    float smoothed_sample = sample + fluid_motion * surface_tension;

    // Create metallic ripples based on amplitude
    float ripple_freq = 15.0 + abs(smoothed_sample) * 25.0;
    float ripples = sin(norm_x * ripple_freq + time * 4.0) * 0.1;
    ripples *= smoothstep(0.0, 0.5, abs(smoothed_sample)); // Ripples appear with amplitude

    // Apply metallic sheen - high frequency oscillations
    float sheen_freq = 30.0 + fft_treble * 40.0;
    float metallic_sheen = sin(norm_x * sheen_freq + time * 6.0) * 0.08;
    metallic_sheen *= abs(cos(norm_x * 8.0 + time * 2.0)); // Modulated intensity

    // Combine all effects
    sample = smoothed_sample + ripples + metallic_sheen;

    // Add droplet formation at peaks
    float droplet_threshold = 0.6;
    if (abs(sample) > droplet_threshold) {
        float droplet_size = (abs(sample) - droplet_threshold) * 3.0;
        float droplet_shape = 1.0 - abs(fract(norm_x * 20.0 + time * 3.0) - 0.5) * 2.0;
        droplet_shape = smoothstep(0.0, 1.0, droplet_shape);
        sample += sign(sample) * droplet_size * droplet_shape * 0.3;
    }

    // Add mercury-like cohesion - samples attract to nearby high amplitudes
    float cohesion_strength = viscosity * 0.4;
    float neighbor_influence = sin(norm_x * 16.0 + time * 2.5) * cohesion_strength;
    sample += neighbor_influence * abs(sample);

    // Final metallic polish - subtle high-frequency detail
    float polish = sin(norm_x * 80.0 + time * 10.0) * 0.03 * fft_treble;
    sample += polish;

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
