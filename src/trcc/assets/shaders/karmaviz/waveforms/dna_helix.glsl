// KarmaViz waveform "dna_helix" (organic): Double helix structure with base pairs
// From KarmaViz 67e8a15 waveforms/organic/dna_helix.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// DNA Helix
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    float total = 0.0;
    float fft = texture(fft_data, vec2(y_coord, 0.5)).r;
    float helix_center_x = 0.5;
    float helix_radius = 0.22 + fft * 0.1;
    float helix_pitch = 12.0 + fft * 4.0;
    float rotation_speed = 5.0 + fft * 3.0;
    // Double helix strands
    for (int strand = 0; strand < 2; strand++) {
        float strand_offset = float(strand) * 3.14159;
        float helix_angle = y_coord * helix_pitch + time * rotation_speed + strand_offset;
        float strand_x = helix_center_x + cos(helix_angle) * helix_radius;
        float strand_dist = abs(x_coord - strand_x);
        if (strand_dist < 0.01) {
            total += (1.0 - strand_dist / 0.01) * (0.5 + fft * 0.5);
        }
    }
    // Base pairs (connecting rungs)
    float rung_spacing = 0.05;
    float rung_y = floor(y_coord / rung_spacing) * rung_spacing;
    if (abs(y_coord - rung_y) < 0.005) {
        float rung_angle = rung_y * helix_pitch + time * rotation_speed;
        float rung_x1 = helix_center_x + cos(rung_angle) * helix_radius;
        float rung_x2 = helix_center_x + cos(rung_angle + 3.14159) * helix_radius;
        float rung_min_x = min(rung_x1, rung_x2);
        float rung_max_x = max(rung_x1, rung_x2);
        if (x_coord >= rung_min_x && x_coord <= rung_max_x) {
            total += 0.3 * (0.5 + fft * 0.5);
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
