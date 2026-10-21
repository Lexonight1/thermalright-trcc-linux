// KarmaViz waveform "flames" (organic): Dynamic flames waveform with flickering, heat distortion, and combustion effects
// From KarmaViz 67e8a15 waveforms/organic/flames.kvwf, MIT (c) 2025 Jason
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

        // Flames waveform - creates dynamic fire effects with flickering and heat distortion
        // Sample FFT for different flame behaviors
        float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;    // Base fire intensity
        float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;    // Flame height
        float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;   // Crackling/sparks

        // Create base flame shape - higher at bottom, flickering at top
        float flame_height = 1.0 - norm_x; // Flames are taller at the base
        float base_intensity = 0.8 + fft_low * 0.4;
    
        // Create multiple flame tongues with different frequencies
        float flame_freq1 = 8.0 + fft_mid * 12.0;
        float flame_freq2 = 15.0 + fft_mid * 20.0;
        float flame_freq3 = 25.0 + fft_high * 30.0;
    
        // Primary flame tongue - main fire body
        float flame1 = sin(norm_x * flame_freq1 + time * 4.0) * flame_height;
        flame1 += sin(norm_x * flame_freq1 * 1.3 + time * 3.2) * flame_height * 0.7;
    
        // Secondary flame tongues - dancing flames
        float flame2 = sin(norm_x * flame_freq2 + time * 6.0 + 1.047) * flame_height * 0.6;
        flame2 += cos(norm_x * flame_freq2 * 0.8 + time * 5.1) * flame_height * 0.4;
    
        // High frequency flickering - flame tips
        float flicker = sin(norm_x * flame_freq3 + time * 8.0) * flame_height * 0.3;
        flicker += sin(norm_x * flame_freq3 * 1.7 + time * 7.3) * flame_height * 0.2;
    
        // Combine flame components
        float flame_pattern = (flame1 + flame2 + flicker) * base_intensity;
    
        // Add heat distortion - wavy air effects
        float heat_freq = 12.0 + fft_mid * 18.0;
        float heat_distortion1 = sin(norm_x * heat_freq + time * 3.0) * 0.15;
        float heat_distortion2 = cos(norm_x * heat_freq * 1.4 + time * 2.3) * 0.1;
        float heat_shimmer = heat_distortion1 + heat_distortion2;
    
        // Apply heat distortion to the sample
        sample += heat_shimmer * abs(sample);
    
        // Add combustion bursts - sudden intensity spikes
        float combustion_threshold = 0.9;
        if (abs(sample) > combustion_threshold && fft_low > 0.3) {
            float burst_intensity = (abs(sample) - combustion_threshold) * 1.9;
            float burst_pattern = sin(time * 10 * norm_x);
            float burst_flicker = fract(sin(norm_x * 73.2 + time * 5.0) * 43758.5453);
            sample += sign(sample) * burst_intensity * burst_pattern * burst_flicker * 0.4;
        }
    
        // Add crackling sounds as visual sparks
        float spark_probability = fft_high * 0.7;
        float spark_noise = fract(sin(norm_x * 150.0 + time * 15.0) * 29847.234) - 0.5;
        spark_noise *= spark_probability * 0.2;
    
        // Create ember effects - glowing particles
        float ember_freq = 40.0 + fft_high * 50.0;
        float ember_pattern = 0.0;
        for (int i = 0; i < 4; i++) {
            float ember_pos = float(i) * 0.25 + fract(time * 0.5 + float(i) * 0.3);
            ember_pos = fract(ember_pos); // Keep in [0,1] range
            float ember_distance = abs(norm_x - ember_pos);
            if (ember_distance < 0.05) {
                float ember_strength = (0.05 - ember_distance) / 0.05;
                ember_pattern += ember_strength * sin(time * 8.0 + float(i)) * 0.3;
            }
        }
    
        // Apply all flame effects to rrthe base sample
        sample = sample * (1.0 + flame_pattern * 0.6);
        sample += spark_noise + ember_pattern;
    
        // Add oxygen-fed intensity variations
        float oxygen_flow = 1.0 + sin(time * 2.0) * 0.2 + fft_low * 0.3;
        sample *= oxygen_flow;
    
        // Create flame color temperature effects (affects amplitude)
        float temperature = 0.8 + fft_mid * 0.4; // Hot flames are more intense
        sample *= temperature;
    
        // Add convection currents - upward flowing motion
        float convection = sin(norm_x * 6.0 + time * 1.5) * 0.1;
        convection *= flame_height * 9; // Stronger at the base
        sample += convection * abs(sample);

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
