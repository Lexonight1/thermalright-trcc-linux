// KarmaViz waveform "spirograph" (mathematical): Beautiful geometric spirograph patterns created by mathematical curves from rolling circles, with multiple overlapping hypotrochoids and epitrochoids
// From KarmaViz 67e8a15 waveforms/mathematical/spirograph.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Spirograph Waveform - Efficient geometric patterns using direct equations

// Fast spirograph intensity using direct mathematical approach
float spirograph_intensity(vec2 pos, float R, float r, float d, float phase, float line_width) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    
    // Convert to polar coordinates
    float angle = atan(p.y, p.x);
    float radius = length(p);
    
    // Spirograph equation in polar form - much more efficient
    float k = r / R;  // ratio of circles
    float l = d / r;  // ratio of drawing point
    
    // Calculate the expected radius at this angle for the spirograph
    float n = (1.0 - k) / k;  // number of cusps
    float spirograph_angle = angle * (1.0 + n) + phase;
    
    // Hypotrochoid equation
    float expected_radius = R * ((1.0 - k) + l * k * cos(spirograph_angle));
    expected_radius = abs(expected_radius) * 0.3; // Scale to fit screen
    
    // Distance from current point to expected spirograph curve
    float dist_to_curve = abs(radius - expected_radius);
    
    // Create smooth line
    return smoothstep(line_width, 0.0, dist_to_curve);
}

// Epitrochoid (rolling outside) intensity
float epitrochoid_intensity(vec2 pos, float R, float r, float d, float phase, float line_width) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    
    float angle = atan(p.y, p.x);
    float radius = length(p);
    
    float k = r / R;
    float l = d / r;
    
    // Epitrochoid has different equation
    float n = (1.0 + k) / k;
    float epi_angle = angle * (1.0 - n) + phase;
    
    float expected_radius = R * ((1.0 + k) - l * k * cos(epi_angle));
    expected_radius = abs(expected_radius) * 0.2; // Scale smaller
    
    float dist_to_curve = abs(radius - expected_radius);
    return smoothstep(line_width, 0.0, dist_to_curve);
}

// Rose curve (another mathematical pattern)
float rose_intensity(vec2 pos, float n, float amplitude, float phase, float line_width) {
    vec2 center = vec2(0.5, 0.5);
    vec2 p = pos - center;
    
    float angle = atan(p.y, p.x) + phase;
    float radius = length(p);
    
    // Rose equation: r = a * cos(n * θ)
    float expected_radius = amplitude * abs(cos(n * angle));
    
    float dist_to_curve = abs(radius - expected_radius);
    return smoothstep(line_width, 0.0, dist_to_curve);
}

// Lissajous-style pattern
float lissajous_intensity(vec2 pos, float a, float b, float phase, float line_width) {
    vec2 center = vec2(0.5, 0.5);
    
    // Lissajous equations: x = A*sin(at + δ), y = B*sin(bt)
    float t = length(pos - center) * 10.0 + phase;
    
    vec2 lissajous_point = vec2(
        0.2 * sin(a * t + phase),
        0.2 * sin(b * t)
    ) + center;
    
    float dist = distance(pos, lissajous_point);
    return smoothstep(line_width, 0.0, dist);
}

float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    vec2 pos = vec2(x_coord, y_coord);
    
    // Sample audio data
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    // Audio-reactive parameters
    float audio_intensity = (abs(audio_sample) + fft_sample) * 0.5;
    float time_factor = time * 0.9;
    
    float total_intensity = 0.0;
    
    // Pattern 1: Main spirograph (hypotrochoid)
    float R1 = 0.8 + sin(time_factor * 0.4) * 0.2;
    float r1 = 0.2 + cos(time_factor * 0.3) * 0.1;
    float d1 = 0.15 + sin(time_factor * 0.5) * 0.05;
    float phase1 = time_factor * 2.0;
    float width1 = 0.01 + audio_intensity * 0.005;
    
    float spiro1 = spirograph_intensity(pos, R1, r1, d1, phase1, width1);
    total_intensity += spiro1 * (0.7 + audio_intensity * 0.5);
    
    // Pattern 2: Secondary spirograph with different ratios
    float R2 = 0.6 + cos(time_factor * 0.35) * 0.15;
    float r2 = 0.15 + sin(time_factor * 0.25) * 0.05;
    float d2 = 0.12 + cos(time_factor * 0.4) * 0.03;
    float phase2 = time_factor * -1.5;
    float width2 = 0.008 + audio_intensity * 0.004;
    
    float spiro2 = spirograph_intensity(pos, R2, r2, d2, phase2, width2);
    total_intensity += spiro2 * (0.5 + audio_intensity * 0.3);
    
    // Pattern 3: Epitrochoid (rolling outside)
    float R3 = 0.4 + sin(time_factor * 0.6) * 0.1;
    float r3 = 0.1 + cos(time_factor * 0.45) * 0.03;
    float d3 = 0.08 + sin(time_factor * 0.35) * 0.01;
    float phase3 = time_factor * 3.0;
    float width3 = 0.0016 + audio_intensity * 0.03;
    
    float epi1 = epitrochoid_intensity(pos, R3, r3, d3, phase3, width3);
    total_intensity += epi1 * (0.4 + audio_intensity * 0.2);
    
    // Pattern 4: Rose curve for variety
    float rose_n = 9.0 + sin(time_factor * 0.2) * 2.0; // 1-5 petals
    float rose_amp = 0.15 + cos(time_factor * 0.3) * 0.05;
    float rose_phase = time_factor * 0.2;
    float rose_width = 0.0918 + audio_intensity * 0.004;
    
    float rose = rose_intensity(pos, rose_n, rose_amp, rose_phase, rose_width);
    total_intensity += rose * (2.0 + audio_intensity * 0.8);
    
    // Pattern 5: Lissajous curves for complexity
    float liss_a = 2.0 + sin(time_factor * 0.55) * 1.0;
    float liss_b = 6.0 + cos(time_factor * 0.18) * 1.5;
    float liss_phase = time_factor * 2.5;
    float liss_width = 0.112 + audio_intensity * 0.006;
    
    float liss = lissajous_intensity(pos, liss_a, liss_b, liss_phase, liss_width);
    total_intensity += liss * (0.25 + audio_intensity * 0.15);
    
    // Audio-reactive center glow
    vec2 center = vec2(0.5, 0.5);
    float center_dist = distance(pos, center);
    if (audio_intensity > 0.2) {
        float glow_radius = 0.25 + audio_intensity * 0.13;
        if (center_dist < glow_radius) {
            total_intensity += (1.0 - center_dist / glow_radius) * audio_intensity * 0.4;
        }
    }
    
    
    return clamp(total_intensity, 0.0, 1.0);
}

// 1D version for compatibility
float compute_waveform_at_x(float x_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Simple 1D spirograph using parametric equations
    float audio_sample = texture(waveform_data, vec2(x_coord, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(x_coord, 0.5)).r;
    
    float t = x_coord * 40.28318 * 2.0 + time * 5.5;
    float R = 20.0;
    float r = 0.3 / fft_sample * 0.2;
    float d = 0.8;
    
    // Hypotrochoid Y component
    float ratio = (R - r) / r;
    float y = (R - r) * sin(t) - d * sin(ratio * t);
    
    // Normalize and add audio
    y = y * 5.2 + 0.5 * audio_sample * 8;
    
    return clamp(abs(y - 0.5) * 2.0, 0.0, 1.0);
}
