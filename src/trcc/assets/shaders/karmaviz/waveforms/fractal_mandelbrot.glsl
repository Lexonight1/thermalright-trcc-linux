// KarmaViz waveform "fractal_mandelbrot" (experimental): Advanced fractal waveform inspired by the Mandelbrot set with complex iteration patterns and escape-time coloring
// From KarmaViz 67e8a15 waveforms/fractal/fractal_mandelbrot.kvwf, MIT (c) 2025 Jason
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

        // Fractal Mandelbrot waveform - complex mathematical beauty
        // Sample FFT for fractal parameters
        float fft_low = texture(fft_data, vec2(0.1, 0.5)).r;
        float fft_mid = texture(fft_data, vec2(0.5, 0.5)).r;
        float fft_high = texture(fft_data, vec2(0.9, 0.5)).r;
    
        // Create complex plane coordinates
        float zoom = 1.0 + fft_mid * 3.0;
        float cx = (norm_x - 0.5) * 4.0 / zoom - 0.5; // Real part
        float cy = sample * 2.0 / zoom; // Imaginary part (based on audio)
    
        // Mandelbrot iteration: z = z² + c
        float zx = 0.0;
        float zy = 0.0;
        float iterations = 0.0;
        float max_iterations = 16.0; // Limited for performance
    
        for (int i = 0; i < 16; i++) {
            if (zx * zx + zy * zy > 4.0) break;
        
            float temp = zx * zx - zy * zy + cx;
            zy = 2.0 * zx * zy + cy;
            zx = temp;
            iterations += 1.0;
        }
    
        // Calculate escape time and smooth coloring
        float escape_value = iterations / max_iterations;
        if (iterations < max_iterations) {
            // Smooth coloring using continuous escape time
            float log_zn = log(zx * zx + zy * zy) / 2.0;
            float nu = log(log_zn / log(2.0)) / log(2.0);
            escape_value = (iterations + 1.0 - nu) / max_iterations;
        }
    
        // Apply fractal transformation to the sample
        float fractal_intensity = escape_value;
    
        // Create multiple fractal layers with different parameters
        float layer1 = fractal_intensity;
    
        // Second layer - Julia set variation
        float julia_cx = -0.7 + fft_low * 0.4;
        float julia_cy = 0.27015 + fft_high * 0.2;
        zx = cx;
        zy = cy;
        iterations = 0.0;
    
        for (int i = 0; i < 12; i++) {
            if (zx * zx + zy * zy > 4.0) break;
        
            float temp = zx * zx - zy * zy + julia_cx;
            zy = 2.0 * zx * zy + julia_cy;
            zx = temp;
            iterations += 1.0;
        }
    
        float layer2 = iterations / 12.0;
    
        // Third layer - Burning Ship fractal
        zx = 0.0;
        zy = 0.0;
        iterations = 0.0;
    
        for (int i = 0; i < 10; i++) {
            if (zx * zx + zy * zy > 4.0) break;
        
            float temp = zx * zx - zy * zy + cx;
            zy = 2.0 * abs(zx * zy) + cy; // abs() creates the "burning ship" effect
            zx = temp;
            iterations += 1.0;
        }
    
        float layer3 = iterations / 10.0;
    
        // Combine fractal layers
        float combined_fractal = (layer1 * 0.5 + layer2 * 0.3 + layer3 * 0.2);
    
        // Apply fractal modulation to the audio sample
        sample = sample * (0.5 + combined_fractal * 1.5);
    
        // Add fractal harmonics - self-similar patterns at different scales
        for (int scale = 1; scale <= 4; scale++) {
            float scale_factor = pow(2.0, float(scale));
            float harmonic_freq = norm_x * scale_factor * 8.0;
            float harmonic_phase = time * (1.0 + float(scale) * 0.5);
        
            // Use fractal value to modulate harmonic intensity
            float harmonic_intensity = combined_fractal / scale_factor;
            float harmonic = sin(harmonic_freq + harmonic_phase) * harmonic_intensity * 0.2;
        
            sample += harmonic;
        }
    
        // Add chaos - sensitive dependence on initial conditions
        float chaos_seed = norm_x * 1000.0 + time * 10.0;
        float chaos = fract(sin(chaos_seed) * 43758.5453);
        if (chaos > 0.95) {
            sample += (chaos - 0.95) * 20.0 * sign(sample) * combined_fractal;
        }
    
        // Apply strange attractor influence
        float attractor_x = norm_x * 2.0 - 1.0;
        float attractor_y = sample;
    
        // Lorenz attractor equations (simplified)
        float sigma = 10.0;
        float rho = 28.0 + fft_mid * 20.0;
        float beta = 8.0 / 3.0;
    
        float dx = sigma * (attractor_y - attractor_x);
        float dy = attractor_x * (rho - attractor_y) - attractor_y;
    
        float attractor_influence = (dx + dy) * 0.01;
        sample += attractor_influence * combined_fractal;
    
        // Add fractal noise - self-similar at all scales
        float fractal_noise = 0.0;
        float amplitude = 0.5;
        float frequency = 4.0;
    
        for (int octave = 0; octave < 6; octave++) {
            fractal_noise += sin(norm_x * frequency + time * 2.0) * amplitude;
            amplitude *= 0.5;
            frequency *= 2.0;
        }
    
        sample += fractal_noise * combined_fractal * 0.3;

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
