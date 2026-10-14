// KarmaViz waveform "quantum_interference" (experimental): Quantum physics-inspired waveform with wave-particle duality, superposition states, and quantum tunneling effects
// From KarmaViz 67e8a15 waveforms/mathematical/quantum_interference.kvwf, MIT (c) 2025 Jason
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

        // Quantum Interference - simulates quantum mechanical wave phenomena
    // Sample FFT for quantum state modulation
    float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;
    float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
    float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;

    // Create quantum wave functions with complex phases
    float quantum_freq1 = 10.0 + fft_low * 15.0;
    float quantum_freq2 = 13.0 + fft_mid * 18.0;
    float quantum_freq3 = 17.0 + fft_high * 22.0;

    // Generate wave functions with quantum phase relationships
    float wave1_real = cos(norm_x * quantum_freq1 + time * 3.0);
    float wave1_imag = sin(norm_x * quantum_freq1 + time * 3.0);
    float wave2_real = cos(norm_x * quantum_freq2 + time * 2.3 + 1.047); // 60° phase
    float wave2_imag = sin(norm_x * quantum_freq2 + time * 2.3 + 1.047);
    float wave3_real = cos(norm_x * quantum_freq3 + time * 1.7 + 2.094); // 120° phase
    float wave3_imag = sin(norm_x * quantum_freq3 + time * 1.7 + 2.094);

    // Calculate probability amplitudes (|ψ|²)
    float prob_amp1 = wave1_real * wave1_real + wave1_imag * wave1_imag;
    float prob_amp2 = wave2_real * wave2_real + wave2_imag * wave2_imag;
    float prob_amp3 = wave3_real * wave3_real + wave3_imag * wave3_imag;

    // Create superposition state
    float superposition_real = (wave1_real + wave2_real + wave3_real) / 3.0;
    float superposition_imag = (wave1_imag + wave2_imag + wave3_imag) / 3.0;
    float superposition_prob = superposition_real * superposition_real + superposition_imag * superposition_imag;

    // Apply quantum interference pattern
    float interference = superposition_prob - (prob_amp1 + prob_amp2 + prob_amp3) / 3.0;
    interference *= 2.0; // Amplify interference effects

    // Simulate wave-particle duality
    float duality_factor = 0.5 + 0.5 * sin(time * 0.8);
    float wave_behavior = interference * duality_factor;
    float particle_behavior = abs(sample) * (1.0 - duality_factor);

    // Apply quantum modulation
    sample = sample + wave_behavior * 0.6 + particle_behavior * 0.4;

    // Add quantum tunneling effect - sudden phase jumps
    float tunnel_probability = fract(sin(norm_x * 23.0 + time * 4.7) * 17389.234);
    if (tunnel_probability > 0.95 && abs(sample) > 0.3) {
        float tunnel_jump = sign(sample) * 0.4 * fft_high;
        sample += tunnel_jump;
    }

    // Create uncertainty principle effects - position-momentum trade-off
    float uncertainty_x = sin(norm_x * 25.0 + time * 5.0) * 0.1;
    float uncertainty_p = cos(norm_x * 35.0 + time * 7.0) * 0.08;
    float uncertainty_product = abs(uncertainty_x * uncertainty_p);
    sample += uncertainty_x - uncertainty_p * sign(sample);

    // Add quantum decoherence - gradual loss of quantum properties
    float coherence_time = 2.0 + fft_mid * 3.0;
    float decoherence = exp(-time / coherence_time) * sin(time * 10.0) * 0.15;
    sample += decoherence * abs(sample);

    // Simulate quantum entanglement - correlated behavior across space
    float entanglement_partner = norm_x + 0.5; // Partner position
    if (entanglement_partner > 1.0) entanglement_partner -= 1.0;
    float partner_phase = entanglement_partner * quantum_freq1 + time * 3.0;
    float entangled_correlation = sin(partner_phase) * 0.1 * fft_low;
    sample += entangled_correlation;

    // Add quantum vacuum fluctuations
    float vacuum_noise = (fract(sin(norm_x * 67.0 + time * 13.7) * 41253.789) - 0.5) * 0.05;
    sample += vacuum_noise;

    // Apply Heisenberg uncertainty to final output
    float final_uncertainty = sin(norm_x * 100.0 + time * 20.0) * 0.03 * sqrt(abs(sample));
    sample += final_uncertainty;

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
