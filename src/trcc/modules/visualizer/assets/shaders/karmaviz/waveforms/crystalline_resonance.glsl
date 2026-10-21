// KarmaViz waveform "crystalline_resonance" (advanced): Crystal-like waveform that creates geometric resonance patterns with harmonic overtones and prismatic reflections
// From KarmaViz 67e8a15 waveforms/advanced/crystalline_resonance.kvwf, MIT (c) 2025 Jason
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

        // Crystalline Resonance - creates geometric crystal-like patterns with harmonic resonance
    // Sample FFT data for harmonic analysis
    float fft_low = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_high = texture(fft_data, vec2(0.5, 0.5)).r;

    // Create crystal lattice structure
    float lattice_freq = 2.0 + fft_mid * 6.0;
    float crystal_x = norm_x * lattice_freq;
    float crystal_pattern = abs(sin(crystal_x)) * abs(cos(crystal_x * 1.618)); // Golden ratio for natural crystal growth

    // Generate harmonic resonance based on FFT
    float harmonic1 = sin(norm_x * 12.0 + time * 5.0) * fft_low;
    float harmonic2 = sin(norm_x * 18.0 + time * 55.0 + 2.094) * fft_mid; // 120 degree phase
    float harmonic3 = sin(norm_x * 24.0 + time * 4.0 + 4.189) * fft_high; // 240 degree phase

    // Combine harmonics with crystal structure
    float resonance = (harmonic1 + harmonic2 + harmonic3) * crystal_pattern;

    // Apply crystalline transformation with faceted edges
    float facet_intensity = 0.3 + fft_mid * 0.4;
    float faceted_sample = sample + resonance * facet_intensity;

    // Create prismatic reflections - multiple phase-shifted copies
    float prism_offset1 = sin(norm_x * 6.0 + time) * 0.1;
    float prism_offset2 = sin(norm_x * 9.0 + time * 1.5 + 1.047) * 0.08; // 60 degree phase
    float prism_offset3 = sin(norm_x * 12.0 + time * 2.0 + 2.094) * 0.06; // 120 degree phase

    // Apply prismatic dispersion
    sample = faceted_sample + prism_offset1 + prism_offset2 + prism_offset3;

    // Add crystal growth spikes at resonant frequencies
    float growth_threshold = 0.7;
    if (abs(sample) > growth_threshold && fft_high > 0.3) {
        float growth_spike = (abs(sample) - growth_threshold) * 2.0;
        float spike_pattern = sin(norm_x * 40.0 + time * 8.0);
        sample += sign(sample) * growth_spike * abs(spike_pattern) * 0.4;
    }

    // Add subtle shimmer effect
    float shimmer = sin(norm_x * 60.0 + time * 12.0) * 0.05 * fft_high;
    sample += shimmer;

        float waveform_value = sample * waveform_scale;

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
