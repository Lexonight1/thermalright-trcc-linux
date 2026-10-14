// KarmaViz waveform "Polar Equations" (mathematical): Rose curves and spirals in polar coordinates
// From KarmaViz 67e8a15 waveforms/mathematical/polar_equations.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Polar Equations
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 center = vec2(0.5, 0.5);
    vec2 pos = vec2(x_coord, y_coord) - center;
    float r = length(pos);
    float theta = atan(pos.y, pos.x);
    float fft = texture(fft_data, vec2(0.5, 0.5)).r;
    // Rose curve: r = cos(k*theta)
    float k = 3.0 + fft * 4.0;
    float rose_r = abs(cos(k * theta + time)) * 0.3;
    if (abs(r - rose_r) < 0.02) {
        return (1.0 - abs(r - rose_r) / 0.02) * (0.6 + fft * 0.4);
    }
    // Spiral: r = a*theta
    float spiral_r = mod(theta + time * 2.0, 6.28318) * 0.05;
    if (abs(r - spiral_r) < 0.015) {
        return (1.0 - abs(r - spiral_r) / 0.015) * 0.5 * fft;
    }
    return 0.0;
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
