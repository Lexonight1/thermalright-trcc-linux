// KarmaViz warp map "Seismic Waves" (motion): Earthquake wave propagation with P-waves, S-waves, and surface waves
// From KarmaViz 67e8a15 warp_maps/motion/seismic_waves.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Simulate seismic wave propagation from multiple epicenters
    vec2 epicenter1 = vec2(0.2, 0.3);
    vec2 epicenter2 = vec2(0.8, 0.7);
    vec2 epicenter3 = vec2(0.5, 0.1);
    
    // Earthquake timing
    float quake1_time = t;
    float quake2_time = t * 2.0;
    float quake3_time = t - 4.0;
    
    vec2 total_seismic_motion = vec2(0.0);
    
    // Process each earthquake
    vec2 epicenters[3] = vec2[3](epicenter1, epicenter2, epicenter3);
    float quake_times[3] = float[3](quake1_time, quake2_time, quake3_time);
    float magnitudes[3] = float[3](1.0, 0.8, 1.2);
    
    for (int i = 0; i < 3; i++) {
        vec2 epicenter = epicenters[i];
        float quake_time = quake_times[i];
        float magnitude = magnitudes[i];
        
        if (quake_time > 0.0) {
            float dist = distance(pos, epicenter);
            
            // P-wave (primary, faster, smaller amplitude)
            float p_wave_speed = 3.0;
            float p_wave_arrival = dist / p_wave_speed;
            if (quake_time > p_wave_arrival) {
                float p_wave_phase = (quake_time - p_wave_arrival) * 15.0;
                float p_wave_amplitude = magnitude * 0.3 * exp(-dist * 2.0) * exp(-(quake_time - p_wave_arrival) * 2.0);
                vec2 p_wave_direction = normalize(pos - epicenter);
                total_seismic_motion += p_wave_direction * sin(p_wave_phase) * p_wave_amplitude;
            }
            
            // S-wave (secondary, slower, larger amplitude)
            float s_wave_speed = 1.8;
            float s_wave_arrival = dist / s_wave_speed;
            if (quake_time > s_wave_arrival) {
                float s_wave_phase = (quake_time - s_wave_arrival) * 8.0;
                float s_wave_amplitude = magnitude * 0.8 * exp(-dist * 1.5) * exp(-(quake_time - s_wave_arrival) * 1.0);
                vec2 s_wave_direction = vec2(-(pos.y - epicenter.y), pos.x - epicenter.x);
                if (length(s_wave_direction) > 0.01) {
                    s_wave_direction = normalize(s_wave_direction);
                }
                total_seismic_motion += s_wave_direction * sin(s_wave_phase) * s_wave_amplitude;
            }
            
            // Surface waves (slowest, largest amplitude, complex motion)
            float surface_wave_speed = 1.2;
            float surface_wave_arrival = dist / surface_wave_speed;
            if (quake_time > surface_wave_arrival) {
                float surface_wave_phase = (quake_time - surface_wave_arrival) * 5.0;
                float surface_wave_amplitude = magnitude * 1.2 * exp(-dist * 1.0) * exp(-(quake_time - surface_wave_arrival) * 0.5);
                
                // Rayleigh wave motion (elliptical)
                vec2 radial = normalize(pos - epicenter);
                vec2 vertical = vec2(0.0, 1.0);
                vec2 rayleigh_motion = radial * cos(surface_wave_phase) + vertical * sin(surface_wave_phase) * 0.5;
                
                total_seismic_motion += rayleigh_motion * surface_wave_amplitude;
            }
        }
    }
    
    // Add background tectonic stress
    float tectonic_phase = t * 0.1 + pos.x * 2.0 + pos.y * 1.5;
    vec2 tectonic_stress = vec2(
        sin(tectonic_phase) * 0.05,
        cos(tectonic_phase * 1.3) * 0.03
    );
    
    return (total_seismic_motion + tectonic_stress) * 0.02;
}
