// KarmaViz waveform "ice" (organic): Crystalline ice waveform with freezing effects, crystal formation, and glacial movements
// From KarmaViz 67e8a15 waveforms/organic/ice.kvwf, MIT (c) 2025 Jason
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

        // Ice waveform - creates crystalline ice effects with freezing and crystal formation
        // Sample FFT for different ice behaviors
        float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;    // Deep freeze/glacial movement
        float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;    // Crystal formation
        float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;   // Ice crackling/surface details

        // Create crystalline structure - hexagonal ice patterns
        float crystal_freq1 = 6.0 + fft_mid * 8.0;  // Hexagonal base frequency
        float crystal_freq2 = 12.0 + fft_mid * 16.0; // Secondary crystal structure
        float crystal_freq3 = 18.0 + fft_high * 24.0; // Fine crystal details
    
        // Primary crystal lattice - hexagonal symmetry
        float crystal1 = sin(norm_x * crystal_freq1 + time * 0.5);
        crystal1 += sin(norm_x * crystal_freq1 * 1.732 + time * 0.3) * 0.8; // √3 for hexagonal
        crystal1 += cos(norm_x * crystal_freq1 * 2.0 + time * 0.7) * 0.6;
    
        // Secondary crystal formation - dendritic patterns
        float crystal2 = cos(norm_x * crystal_freq2 + time * 0.2);
        crystal2 += sin(norm_x * crystal_freq2 * 0.618 + time * 0.4) * 0.7; // Golden ratio
        crystal2 *= 0.5;
    
        // Fine ice surface details - frost patterns
        float frost = sin(norm_x * crystal_freq3 + time * 0.1) * 0.3;
        frost += cos(norm_x * crystal_freq3 * 1.414 + time * 0.15) * 0.2; // √2
    
        // Combine crystal structures
        float crystal_pattern = (crystal1 + crystal2 + frost) * 0.3;
    
        // Create freezing effect - gradual solidification
        float freeze_rate = 0.8 + fft_low * 0.4;
        float freezing_factor = 1.0 - freeze_rate * 0.3;
    
        // Add thermal contraction - ice shrinking as it cools
        float thermal_freq = 4.0 + fft_low * 6.0;
        float contraction = sin(norm_x * thermal_freq + time * 0.8) * 0.1;
        contraction *= freeze_rate;
    
        // Apply freezing effects to the sample
        sample *= freezing_factor;
        sample += contraction * abs(sample);
    
        // Add ice crackling - stress fractures
        float crack_threshold = 0.6;
        if (abs(sample) > crack_threshold && fft_high > 0.2) {
            float crack_intensity = (abs(sample) - crack_threshold) * 1.5;
            float crack_pattern = sign(sin(norm_x * 80.0 + time * 3.0));
            float crack_randomness = fract(sin(norm_x * 127.3 + time * 2.0) * 43758.5453) - 0.5;
            sample += crack_pattern * crack_intensity * crack_randomness * 0.2;
        }
    
        // Create glacial movement - slow, massive shifts
        float glacial_freq = 2.0 + fft_low * 3.0;
        float glacial_movement = sin(norm_x * glacial_freq + time * 0.3) * 0.15;
        glacial_movement += cos(norm_x * glacial_freq * 0.5 + time * 0.2) * 0.1;
    
        // Add sublimation effects - ice directly turning to vapor
        float sublimation_rate = fft_high * 0.3;
        float vapor_pattern = fract(sin(norm_x * 200.0 + time * 4.0) * 29847.234) - 0.5;
        vapor_pattern *= sublimation_rate * 0.1;
    
        // Create ice crystal growth patterns
        float growth_freq = 10.0 + fft_mid * 15.0;
        float growth_pattern = 0.0;
        for (int i = 0; i < 6; i++) { // 6-fold symmetry like real ice crystals
            float angle = float(i) * 1.047; // 60 degrees in radians
            float growth_wave = sin(norm_x * growth_freq + time * 0.6 + angle);
            growth_pattern += growth_wave * (1.0 / 6.0);
        }
        growth_pattern *= fft_mid * 0.2;
    
        // Apply all ice effects to the base sample
        sample = sample + crystal_pattern;
        sample += glacial_movement + vapor_pattern + growth_pattern;
    
        // Add temperature-dependent stiffness
        float temperature_factor = 0.7 + fft_low * 0.2; // Colder = stiffer
        sample *= temperature_factor;
    
        // Create refractive index variations - light bending through ice
        float refraction = sin(norm_x * 8.0 + time * 0.4) * 0.08;
        refraction *= abs(sample); // Stronger effect with higher amplitude
        sample += refraction;
    
        // Add pressure ridge formation - ice sheets colliding
        float pressure_threshold = 0.5;
        if (abs(sample) > pressure_threshold && fft_low > 0.4) {
            float ridge_intensity = (abs(sample) - pressure_threshold) * 2.0;
            float ridge_pattern = sin(norm_x * 20.0 + time * 1.0);
            sample += sign(sample) * ridge_intensity * ridge_pattern * 0.3;
        }
    
        // Apply overall ice crystallization effect
        float crystallization = 1.0 + crystal_pattern * 0.4;
        sample *= crystallization;

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
