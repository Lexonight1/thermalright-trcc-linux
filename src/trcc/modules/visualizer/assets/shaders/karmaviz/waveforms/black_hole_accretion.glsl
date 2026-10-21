// KarmaViz waveform "black_hole_accretion" (cosmic): Black hole accretion disk dynamics
// From KarmaViz 67e8a15 waveforms/cosmic/black_hole_accretion.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }

    // Original waveform logic converted to inline
    if (!waveform_enabled || waveform_length <= 0) {
            float waveform_value = 0.0;
        }
    
        float norm_x = clamp(x_coord, 0.0, 1.0);
        float sample = texture(waveform_data, vec2(norm_x, 0.5)).r;
    
        // Event horizon at center
        float event_horizon = 0.5;
        float distance_to_bh = abs(norm_x - event_horizon);
    
        // Accretion disk
        float disk_radius = 0.3;
        float in_disk = step(distance_to_bh, disk_radius);
    
        // Orbital velocity (faster closer to black hole)
        float orbital_speed = 1.0 / (distance_to_bh + 0.1);
        float orbital_phase = time * orbital_speed;
    
        // Plasma heating
        float plasma_temp = abs(sample) / (distance_to_bh + 0.1);
    
        // Gravitational redshift
        float redshift = 1.0 - 1.0 / sqrt(1.0 + distance_to_bh);
    
        // Jets from poles
        float jet_intensity = exp(-pow(distance_to_bh * 10.0, 2.0)) * abs(sample);
    
        float accretion = in_disk * plasma_temp * redshift + jet_intensity;
        float waveform_value = accretion * sin(orbital_phase) * waveform_scale;

    // Render as horizontal waveform line
    float waveform_y = 0.5 + waveform_value * 0.25; // Center at 0.5 with amplitude scaling

    // Create a line with thickness and glow
    float line_thickness = 0.02;
    float distance_to_line = abs(y_coord - waveform_y);

    // Core line intensity
    float core_intensity = smoothstep(line_thickness, 0.0, distance_to_line);

    // Glow effect
    float glow_intensity = exp(-distance_to_line * distance_to_line / (glow_radius * glow_radius)) * 0.3;

    return clamp(core_intensity + glow_intensity, 0.0, 1.0);
}
