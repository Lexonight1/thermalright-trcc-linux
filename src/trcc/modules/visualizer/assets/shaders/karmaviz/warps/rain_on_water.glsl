// KarmaViz warp map "rain_on_water" (organic): Realistic rain falling onto still water with droplet impacts, splash effects, and expanding ripples that fade over time
// From KarmaViz 67e8a15 warp_maps/organic/rain_on_water.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Rain on Water - Realistic droplet impacts with expanding ripples
vec2 get_pattern(vec2 pos, float t) {
    vec2 total_displacement = vec2(0.0);
    
    // Rain parameters - tweaked for more realistic rain
    float rain_intensity = 0.05;     // Increased intensity
    float ripple_strength = 3.5;    // Stronger ripples
    float ripple_speed = 0.015;     // Faster ripples
    float ripple_decay = 0.8;       // Slower decay for longer-lasting ripples
    
    // More raindrops for denser rain effect
    for (int i = 0; i < 7; i++) {
        float drop_id = float(i);
        
        // Improved random distribution of drops
        float drop_x = fract(sin(drop_id * 78.233 + t * 0.1) * 43758.5453);
        float drop_y = fract(sin(drop_id * 12.9898 - t * 0.05) * 43758.5453);
        vec2 drop_pos = vec2(drop_x, drop_y);
        
        // More varied timing for natural feel
        float time_offset = fract(sin(drop_id * 45.123) * 43758.5453) * 8.0;
        float drop_cycle = 2.0 + sin(drop_id * 3.14) * 0.5;  // Shorter cycles
        float drop_time = mod(t + time_offset, drop_cycle);
        
        float dist_to_drop = distance(pos, drop_pos);
        float ripple_radius = drop_time * ripple_speed;
        float ripple_distance = abs(dist_to_drop - ripple_radius);
        
        // Enhanced ripple effect
        if (ripple_distance < 0.1 && drop_time < 2.5) {
            float ring_sharpness = exp(-ripple_distance * 30.0);  // Sharper rings
            float time_fade = exp(-drop_time * ripple_decay);
            float ripple_intensity = ring_sharpness * time_fade * rain_intensity;
            
            vec2 ripple_direction = normalize(pos - drop_pos);
            vec2 tangent = vec2(-ripple_direction.y, ripple_direction.x);
            
            // More dynamic primary ripple
            float primary_ripple = sin(ripple_radius * 20.0 - drop_time * 12.0) * ripple_intensity;
            total_displacement += ripple_direction * primary_ripple * ripple_strength;
            
            // Enhanced secondary waves
            float secondary_ripple = cos(ripple_radius * 15.0 - drop_time * 8.0) * ripple_intensity * 0.4;
            total_displacement += tangent * secondary_ripple * ripple_strength;
            
            // More dramatic splash effect
            if (drop_time < 0.4 && dist_to_drop < 0.06) {
                float splash_intensity = (0.4 - drop_time) / 0.4;
                float splash_radius = 1.0 - (dist_to_drop / 0.06);
                vec2 splash = ripple_direction * splash_intensity * splash_radius * 0.04;
                float splash_chaos = sin(drop_id * 20.0 + drop_time * 30.0) * 0.7;
                splash *= (1.0 + splash_chaos);
                total_displacement += splash;
            }
        }
        
        // Enhanced secondary ripples
        for (int ring = 1; ring <= 3; ring++) {
            float ring_delay = float(ring) * 0.12;
            float ring_time = drop_time - ring_delay;
            
            if (ring_time > 0.0 && ring_time < 2.0) {
                float ring_radius = ring_time * ripple_speed * 0.85;
                float ring_distance = abs(dist_to_drop - ring_radius);
                
                if (ring_distance < 0.08) {
                    float ring_sharpness = exp(-ring_distance * 25.0);
                    float ring_fade = exp(-ring_time * (ripple_decay + float(ring) * 0.3));
                    float ring_intensity = ring_sharpness * ring_fade * rain_intensity * 0.7;
                    
                    vec2 ring_direction = normalize(pos - drop_pos);
                    float ring_wave = sin(ring_radius * 22.0 - ring_time * 12.0) * ring_intensity;
                    total_displacement += ring_direction * ring_wave * ripple_strength * 0.8;
                }
            }
        }
    }
    
    // Enhanced background water movement
    float background_wave_x = sin(pos.x * 10.0 + t * 0.7) * cos(pos.y * 8.0 + t * 0.4) * 0.003;
    float background_wave_y = cos(pos.x * 8.0 + t * 0.5) * sin(pos.y * 10.0 + t * 0.8) * 0.003;
    total_displacement += vec2(background_wave_x, background_wave_y);
    
    // Enhanced surface tension
    float surface_tension = sin(pos.x * 30.0 + pos.y * 30.0 + t * 0.3) * 0.001;
    total_displacement += vec2(surface_tension, surface_tension * 0.8);
    
    return total_displacement;
}
