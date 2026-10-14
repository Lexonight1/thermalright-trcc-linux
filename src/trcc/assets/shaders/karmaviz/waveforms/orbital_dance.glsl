// KarmaViz waveform "Orbital Dance" (motion): Particles in orbital motion with trails
// From KarmaViz 67e8a15 waveforms/motion/orbital_dance.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Orbital Dance
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    vec2 center = vec2(0.5, 0.5);
    float total = 0.0;
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float fft = texture(fft_data, vec2(fi / 6.0, 0.5)).r;
        float orbit_radius = 0.1 + fi * 0.05;
        float orbit_speed = 1.0 + fi * 0.3 + fft * 2.0;
        float angle = time * orbit_speed + fi * 1.047;
        vec2 orbit_pos = center + vec2(cos(angle), sin(angle)) * orbit_radius;
        float particle_size = 0.02 + fft * 0.03;
        float dist = distance(vec2(x_coord, y_coord), orbit_pos);
        if (dist < particle_size) {
            total += (1.0 - dist / particle_size) * (0.4 + fft * 0.6);
        }
        // Orbital trails
        for (int t = 1; t < 8; t++) {
            float trail_angle = angle - float(t) * 0.2;
            vec2 trail_pos = center + vec2(cos(trail_angle), sin(trail_angle)) * orbit_radius;
            float trail_dist = distance(vec2(x_coord, y_coord), trail_pos);
            if (trail_dist < particle_size * 0.5) {
                total += (1.0 - trail_dist / (particle_size * 0.5)) * 0.2 / float(t);
            }
        }
    }
    return clamp(total, 0.0, 1.0);
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
