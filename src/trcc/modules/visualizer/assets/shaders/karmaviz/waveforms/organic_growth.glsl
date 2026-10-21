// KarmaViz waveform "organic_growth" (natural): Biological growth patterns inspired by plant tendrils, root systems, and cellular division with fibonacci spirals
// From KarmaViz 67e8a15 waveforms/organic/organic_growth.kvwf, MIT (c) 2025 Jason
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

        // Organic Growth - simulates natural biological growth patterns
    // Sample FFT for growth rate and branching control
    float fft_nutrients = texture(fft_data, vec2(0.15, 0.5)).r; // Low freq = nutrients
    float fft_energy = texture(fft_data, vec2(0.6, 0.5)).r;     // Mid freq = growth energy
    float fft_stress = texture(fft_data, vec2(0.85, 0.5)).r;    // High freq = environmental stress

    // Define growth parameters
    float growth_rate = 1.0 + fft_nutrients * 3.0;
    float branch_probability = 0.3 + fft_energy * 0.4;
    float stress_factor = fft_stress * 0.6;

    // Create main growth stem using golden ratio spiral
    float phi = 1.618033988749; // Golden ratio
    float spiral_angle = norm_x * phi * 4.0 + time * growth_rate;
    float main_stem = sin(spiral_angle) * 0.4;

    // Add organic curvature - plants don't grow perfectly straight
    float curvature1 = sin(norm_x * 3.0 + time * 0.8) * 0.2;
    float curvature2 = cos(norm_x * 5.0 + time * 0.6 + 1.2) * 0.15;
    float organic_curve = curvature1 + curvature2;

    // Apply growth to base sample
    sample = sample + main_stem + organic_curve;

    // Create branching patterns - fibonacci sequence based
    float branch_positions[8];
    branch_positions[0] = 0.0;
    branch_positions[1] = 0.125;  // 1/8
    branch_positions[2] = 0.25;   // 2/8
    branch_positions[3] = 0.375;  // 3/8
    branch_positions[4] = 0.5;    // 4/8
    branch_positions[5] = 0.625;  // 5/8
    branch_positions[6] = 0.75;   // 6/8
    branch_positions[7] = 0.875;  // 7/8

    // Generate branches at fibonacci positions
    float total_branching = 0.0;
    for (int i = 0; i < 8; i++) {
        float branch_center = branch_positions[i];
        float distance_to_branch = abs(norm_x - branch_center);
    
        if (distance_to_branch < 0.05) { // Branch influence radius
            float branch_strength = (0.05 - distance_to_branch) / 0.05;
            float branch_angle = float(i) * 0.785398 + time * 2.0; // 45° increments
            float branch_growth = sin(branch_angle) * branch_strength * branch_probability;
            total_branching += branch_growth;
        }
    }

    sample += total_branching * 0.3;

    // Add root system - underground growth (negative direction)
    float root_depth = sin(norm_x * 7.0 - time * growth_rate * 0.5) * 0.25;
    float root_branching = cos(norm_x * 11.0 - time * growth_rate * 0.3 + 2.1) * 0.15;
    float root_system = (root_depth + root_branching) * fft_nutrients;
    sample -= abs(root_system); // Roots grow downward

    // Simulate cellular division - periodic growth spurts
    float cell_cycle = 4.0 + fft_energy * 2.0; // Division cycle time
    float division_phase = fract(time / cell_cycle);
    float mitosis_boost = 0.0;
    if (division_phase > 0.8) { // Mitosis phase
        mitosis_boost = (division_phase - 0.8) / 0.2; // Ramp up during division
        mitosis_boost = sin(mitosis_boost * 3.14159) * 0.3; // Smooth pulse
    }
    sample += mitosis_boost * abs(sample);

    // Add phototropism - growth toward light (higher frequencies)
    float light_direction = fft_energy;
    float phototropic_bend = sin(norm_x * 6.0 + time * 1.5) * light_direction * 0.2;
    sample += phototropic_bend;

    // Environmental stress response - adapt growth pattern
    if (stress_factor > 0.5) {
        // High stress - more compact, defensive growth
        float stress_compression = (stress_factor - 0.5) * 2.0;
        sample *= (1.0 - stress_compression * 0.3);
    
        // Add stress-induced trembling
        float stress_tremor = sin(norm_x * 40.0 + time * 8.0) * stress_compression * 0.1;
        sample += stress_tremor;
    } else {
        // Low stress - expansive, reaching growth
        float expansion_factor = (0.5 - stress_factor) * 2.0;
        sample *= (1.0 + expansion_factor * 0.2);
    }

    // Add seasonal growth variation
    float seasonal_cycle = sin(time * 0.1) * 0.15; // Very slow seasonal change
    sample += seasonal_cycle * abs(sample);

    // Simulate aging - gradual stiffening of movement
    float age_factor = min(1.0, time * 0.05); // Gradual aging
    float flexibility = 1.0 - age_factor * 0.4;
    sample *= flexibility;

    // Add natural randomness - genetic variation
    float genetic_noise = fract(sin(norm_x * 31.7 + time * 0.3) * 15485.863) - 0.5;
    sample += genetic_noise * 0.08 * fft_nutrients;

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
