// KarmaViz waveform "spectrogram" (analytical): Full spectrogram visualization displaying frequency content over time with waterfall effects and realistic spectral analysis
// From KarmaViz 67e8a15 waveforms/experimental/spectrogram.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }

    // Original waveform logic converted to inline
    if (!waveform_enabled || waveform_length <= 0) {
            float waveform_value = 0.0;
        }

        // Full spectrogram visualization
        // X-axis represents frequency (0 = low freq, 1 = high freq)
        // Y-axis (amplitude) represents spectral intensity over time
    
        float norm_x = clamp(x_coord, 0.0, 1.0);
    
        // Map X coordinate to frequency bands
        float frequency_band = norm_x;
    
        // Create spectral analysis using multiple waveform samples
        float spectral_intensity = 0.0;
        int num_analysis_points = 16;
    
        // Analyze the waveform to extract frequency content
        for (int i = 0; i < num_analysis_points; i++) {
            float analysis_pos = float(i) / float(num_analysis_points - 1);
            float waveform_sample = texture(waveform_data, vec2(analysis_pos, 0.5)).r;
        
            // Create frequency-selective filtering
            // Simulate different frequency bands responding to the waveform
            float freq_response = 0.0;
        
            // Low frequency response (0.0 - 0.3)
            if (frequency_band <= 0.3) {
                float low_freq_factor = (0.3 - frequency_band) / 0.3;
                // Low frequencies respond more to sustained, low-frequency content
                freq_response += abs(waveform_sample) * low_freq_factor * 1.5;
            
                // Add sub-harmonic content
                float sub_harmonic = sin(analysis_pos * 6.28318 * 2.0 + time * 2.0);
                freq_response += abs(sub_harmonic * waveform_sample) * low_freq_factor * 0.3;
            }
        
            // Mid frequency response (0.2 - 0.7)
            if (frequency_band >= 0.2 && frequency_band <= 0.7) {
                float mid_range = 0.5;
                float mid_freq_factor = 1.0 - abs(frequency_band - mid_range) / mid_range;
                mid_freq_factor = max(mid_freq_factor, 0.0);
            
                // Mid frequencies respond to harmonic content
                for (int h = 2; h <= 6; h++) {
                    float harmonic_phase = analysis_pos * 6.28318 * float(h) + time * 3.0;
                    float harmonic = sin(harmonic_phase + frequency_band * 10.0);
                    freq_response += abs(harmonic * waveform_sample) * mid_freq_factor * (1.0 / float(h));
                }
            }
        
            // High frequency response (0.6 - 1.0)
            if (frequency_band >= 0.6) {
                float high_freq_factor = (frequency_band - 0.6) / 0.4;
                // High frequencies respond to transients and noise-like content
                float transient_detector = abs(waveform_sample);
            
                // Add high-frequency harmonics and noise
                float high_freq_noise = sin(analysis_pos * 6.28318 * 20.0 + time * 8.0 + frequency_band * 50.0);
                high_freq_noise *= sin(time * 15.0 + frequency_band * 30.0);
            
                freq_response += transient_detector * high_freq_factor * 1.2;
                freq_response += abs(high_freq_noise * waveform_sample) * high_freq_factor * 0.4;
            }
        
            // Weight by analysis position to create time-based variation
            float time_weight = sin(analysis_pos * 6.28318 + time * 1.5) * 0.3 + 0.7;
            spectral_intensity += freq_response * time_weight;
        }
    
        spectral_intensity /= float(num_analysis_points);
    
        // Add time-based waterfall effect
        float waterfall_speed = 3.0;
        float time_phase = time * waterfall_speed + frequency_band * 5.0;
        float waterfall_modulation = sin(time_phase) * 0.2 + 0.8;
        spectral_intensity *= waterfall_modulation;
    
        // Create frequency-dependent coloring and intensity
        float freq_coloring = 1.0;
        if (frequency_band < 0.2) {
            // Bass frequencies - deeper, more sustained
            freq_coloring = 1.4;
            spectral_intensity *= 1.3;
        } else if (frequency_band < 0.6) {
            // Mid frequencies - balanced
            freq_coloring = 1.0;
        } else {
            // High frequencies - brighter, more transient
            freq_coloring = 0.9;
            spectral_intensity *= 1.1;
        }
    
        // Add spectral peaks - emphasize strong frequency components
        float peak_detector = spectral_intensity;
        if (peak_detector > 0.6) {
            float peak_enhancement = (peak_detector - 0.6) / 0.4;
            spectral_intensity += peak_enhancement * 0.5;
        }
    
        // Apply logarithmic scaling for realistic spectrogram appearance
        spectral_intensity = log(1.0 + spectral_intensity * 8.0) / log(9.0);
    
        // Add spectral flux - rate of change in spectrum over time
        float flux_frequency = 25.0;
        float spectral_flux = sin(frequency_band * flux_frequency + time * 6.0) * 0.15;
        spectral_flux *= spectral_intensity; // Modulate by current spectral content
        spectral_intensity += spectral_flux;
    
        // Create realistic spectrogram noise floor
        float noise_floor = 0.08;
        spectral_intensity = max(spectral_intensity, noise_floor);
    
        // Add temporal coherence - smooth transitions over time
        float temporal_smoothing = sin(time * 0.8 + frequency_band * 2.0) * 0.1 + 0.9;
        spectral_intensity *= temporal_smoothing;
    
        // Convert spectral intensity to waveform amplitude
        float spectrogram_amplitude = spectral_intensity * 2.0 - 1.0; // Convert to [-1, 1] range
    
        // Apply frequency-dependent amplitude scaling
        spectrogram_amplitude *= freq_coloring;
    
        // Add dynamic range compression for better visibility
        float compression_ratio = 0.7;
        if (abs(spectrogram_amplitude) > compression_ratio) {
            float excess = abs(spectrogram_amplitude) - compression_ratio;
            float compressed_excess = excess * 0.3; // Compress the excess
            spectrogram_amplitude = sign(spectrogram_amplitude) * (compression_ratio + compressed_excess);
        }

        float waveform_value = spectrogram_amplitude * waveform_scale;

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
