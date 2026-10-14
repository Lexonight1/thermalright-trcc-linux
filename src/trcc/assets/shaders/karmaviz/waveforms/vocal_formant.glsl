// KarmaViz waveform "vocal_formant" (natural): Human voice-inspired waveform that simulates vocal formants, harmonics, and speech patterns
// From KarmaViz 67e8a15 waveforms/natural/vocal_formant.kvwf, MIT (c) 2025 Jason
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

        // Vocal Formant waveform - simulates human voice characteristics
        // Sample FFT for different vocal characteristics
        float fft_fundamental = texture(fft_data, vec2(0.1, 0.5)).r;  // Fundamental frequency
        float fft_formant1 = texture(fft_data, vec2(0.3, 0.5)).r;    // First formant (vowel)
        float fft_formant2 = texture(fft_data, vec2(0.5, 0.5)).r;    // Second formant (vowel)
        float fft_formant3 = texture(fft_data, vec2(0.7, 0.5)).r;    // Third formant (consonant)
        float fft_fricative = texture(fft_data, vec2(0.9, 0.5)).r;   // High freq (fricatives)
    
        // Define vocal tract parameters
        float vocal_tract_length = 17.5; // cm, average adult
        float formant_bandwidth = 50.0;  // Hz, formant bandwidth
    
        // Calculate formant frequencies (simplified vocal tract model)
        // F1: 500-700 Hz (tongue height), F2: 1000-2500 Hz (tongue position)
        float f1_base = 500.0 + fft_formant1 * 200.0;  // 500-700 Hz
        float f2_base = 1000.0 + fft_formant2 * 1500.0; // 1000-2500 Hz
        float f3_base = 2500.0 + fft_formant3 * 1000.0; // 2500-3500 Hz
    
        // Convert to normalized frequencies for our waveform
        float f1_norm = f1_base / 4000.0;  // Normalize to [0,1] range
        float f2_norm = f2_base / 4000.0;
        float f3_norm = f3_base / 4000.0;
    
        // Create vowel-like formant structure
        float vowel_time = time * 0.8; // Slower vowel transitions
    
        // Simulate different vowel sounds based on formant positions
        float vowel_morph = sin(vowel_time) * 0.5 + 0.5; // 0-1 range
    
        // Vowel formant patterns (simplified)
        float vowel_f1, vowel_f2;
        if (vowel_morph < 0.2) {
            // /i/ (ee) - high front vowel
            vowel_f1 = 0.15; vowel_f2 = 0.6;
        } else if (vowel_morph < 0.4) {
            // /e/ (eh) - mid front vowel
            vowel_f1 = 0.25; vowel_f2 = 0.5;
        } else if (vowel_morph < 0.6) {
            // /a/ (ah) - low central vowel
            vowel_f1 = 0.4; vowel_f2 = 0.35;
        } else if (vowel_morph < 0.8) {
            // /o/ (oh) - mid back vowel
            vowel_f1 = 0.3; vowel_f2 = 0.25;
        } else {
            // /u/ (oo) - high back vowel
            vowel_f1 = 0.2; vowel_f2 = 0.2;
        }
    
        // Apply formant filtering to the sample
        float formant1_response = exp(-abs(norm_x - vowel_f1) * 20.0); // Gaussian-like response
        float formant2_response = exp(-abs(norm_x - vowel_f2) * 15.0);
        float formant3_response = exp(-abs(norm_x - f3_norm) * 10.0);
    
        // Combine formant responses
        float formant_filter = formant1_response * 0.5 + formant2_response * 0.3 + formant3_response * 0.2;
    
        // Apply vocal tract resonance
        sample = sample * (0.3 + formant_filter * 1.4);
    
        // Add harmonic series (vocal fold vibration)
        float fundamental_freq = 8.0 + fft_fundamental * 12.0;
        float harmonic_series = 0.0;
    
        for (int h = 1; h <= 8; h++) {
            float harmonic_freq = fundamental_freq * float(h);
            float harmonic_amplitude = 1.0 / float(h); // Natural harmonic rolloff
        
            // Apply formant shaping to harmonics
            float harmonic_pos = float(h) / 8.0;
            float harmonic_formant = exp(-abs(harmonic_pos - vowel_f1) * 10.0) * 0.4 +
                                    exp(-abs(harmonic_pos - vowel_f2) * 8.0) * 0.3;
        
            harmonic_series += sin(norm_x * harmonic_freq + time * 3.0) * 
                              harmonic_amplitude * harmonic_formant * 0.15;
        }
    
        sample += harmonic_series;
    
        // Add vocal fry (low frequency irregularity)
        if (fft_fundamental > 0.6) {
            float fry_freq = 2.0 + fft_fundamental * 4.0;
            float vocal_fry = sin(norm_x * fry_freq + time * 1.5) * 0.1;
            vocal_fry *= fract(sin(norm_x * 13.7 + time * 2.3) * 43758.5453); // Add irregularity
            sample += vocal_fry;
        }
    
        // Add breathiness (aspiration noise)
        float breathiness = fft_fricative * 0.3;
        float breath_noise = (fract(sin(norm_x * 100.0 + time * 20.0) * 43758.5453) - 0.5) * breathiness;
        sample += breath_noise;
    
        // Simulate consonant articulation
        float consonant_time = fract(time * 2.0); // Faster consonant changes
        if (consonant_time < 0.1) {
            // Plosive (p, t, k) - sudden burst
            float plosive_burst = exp(-consonant_time * 50.0) * 0.5;
            sample += plosive_burst * sign(sample);
        } else if (consonant_time > 0.8) {
            // Fricative (s, f, sh) - high frequency noise
            float fricative_noise = sin(norm_x * 80.0 + time * 15.0) * fft_fricative * 0.3;
            sample += fricative_noise;
        }
    
        // Add vocal tract coupling (nasal resonance)
        if (sin(time * 0.3) > 0.5) { // Intermittent nasal coupling
            float nasal_freq = 12.0 + fft_formant1 * 8.0;
            float nasal_resonance = sin(norm_x * nasal_freq + time * 2.0) * 0.15;
            sample += nasal_resonance;
        }
    
        // Apply vocal effort (dynamic range compression like human voice)
        float vocal_effort = 0.7 + fft_fundamental * 0.3;
        sample = tanh(sample * vocal_effort) * 0.8; // Soft limiting
    
        // Add vibrato (pitch modulation)
        float vibrato_rate = 4.5 + sin(time * 0.1) * 1.5; // 3-6 Hz vibrato
        float vibrato_depth = fft_formant2 * 0.1;
        float vibrato = sin(time * vibrato_rate) * vibrato_depth;
        sample *= (1.0 + vibrato);

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
