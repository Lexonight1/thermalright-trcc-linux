// KarmaViz waveform "plasma" (advanced): Enhanced plasma waveform with magnetic field interactions, plasma instabilities, and fusion reactions
// From KarmaViz 67e8a15 waveforms/organic/plasma.kvwf, MIT (c) 2025 Jason
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

        // Enhanced Plasma waveform - simulates advanced plasma physics
        // Sample FFT for different plasma behaviors
        float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;    // Ion motion
        float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;    // Electron dynamics
        float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;   // High-energy particles

        // Create magnetic field lines - helical structure
        float magnetic_freq = 8.0 + fft_mid * 12.0;
        float field_line1 = sin(norm_x * magnetic_freq + time * 3.0);
        float field_line2 = sin(norm_x * magnetic_freq * 1.618 + time * 2.3 + 1.047); // Golden ratio + 60°
        float field_line3 = sin(norm_x * magnetic_freq * 2.618 + time * 1.7 + 2.094); // Fibonacci + 120°
    
        // Combine magnetic field components
        float magnetic_field = (field_line1 + field_line2 * 0.8 + field_line3 * 0.6) / 2.4;
    
        // Simulate plasma instabilities - Kelvin-Helmholtz and Rayleigh-Taylor
        float instability_freq = 15.0 + fft_high * 25.0;
        float kh_instability = sin(norm_x * instability_freq + time * 6.0) * cos(norm_x * instability_freq * 0.7 + time * 4.0);
        float rt_instability = sin(norm_x * instability_freq * 1.3 + time * 5.0) * sin(norm_x * instability_freq * 0.9 + time * 7.0);
    
        // Plasma turbulence cascade
        float turbulence = 0.0;
        for (int i = 1; i <= 4; i++) {
            float scale = pow(2.0, float(i));
            float amplitude = 1.0 / scale;
            turbulence += sin(norm_x * scale * 20.0 + time * (3.0 + float(i))) * amplitude;
        }
        turbulence *= fft_mid * 0.3;
    
        // Ion-electron coupling effects
        float ion_freq = 6.0 + fft_low * 10.0;
        float electron_freq = 40.0 + fft_high * 60.0;
        float ion_motion = sin(norm_x * ion_freq + time * 2.0) * 0.4;
        float electron_motion = sin(norm_x * electron_freq + time * 8.0) * 0.15;
    
        // Plasma confinement - particles follow magnetic field lines
        float confinement_strength = 0.7 + fft_mid * 0.3;
        float confined_motion = magnetic_field * confinement_strength;
    
        // Apply all plasma effects to the base sample
        sample = sample + confined_motion * 0.5;
        sample += (kh_instability + rt_instability) * 0.2;
        sample += turbulence;
        sample += ion_motion + electron_motion;
    
        // Simulate fusion reactions - energy bursts at high density regions
        float density_threshold = 0.7;
        if (abs(sample) > density_threshold && fft_low > 0.3) {
            float fusion_energy = (abs(sample) - density_threshold) * 4.0;
            float fusion_burst = exp(-abs(fract(norm_x * 30.0 + time * 5.0) - 0.5) * 10.0);
            sample += sign(sample) * fusion_energy * fusion_burst * 0.6;
        }
    
        // Add plasma oscillations - Langmuir waves
        float langmuir_freq = 50.0 + fft_high * 100.0;
        float langmuir_waves = sin(norm_x * langmuir_freq + time * 12.0) * 0.1;
        langmuir_waves *= smoothstep(0.2, 0.8, abs(sample)); // Only in dense regions
        sample += langmuir_waves;
    
        // Magnetic reconnection events - sudden topology changes
        float reconnection_prob = fract(sin(norm_x * 17.3 + time * 3.7) * 29847.234);
        if (reconnection_prob > 0.97 && fft_mid > 0.5) {
            float reconnection_burst = sin(time * 15.0) * 0.8;
            sample += reconnection_burst * sign(sample);
        }
    
        // Add coronal heating - high-frequency energy injection
        float heating_freq = 80.0 + fft_high * 120.0;
        float coronal_heating = sin(norm_x * heating_freq + time * 10.0) * 0.08;
        coronal_heating *= fft_high; // Intensity based on high frequencies
        sample += coronal_heating;
    
        // Plasma beta effects - pressure balance
        float plasma_beta = fft_low / (fft_mid + 0.1); // Ratio of thermal to magnetic pressure
        float beta_modulation = 1.0 + plasma_beta * 0.3;
        sample *= beta_modulation;

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
