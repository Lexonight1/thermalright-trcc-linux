// KarmaViz warp map "Mandelbrot Flythrough" (fractal): Dynamic journey through the Mandelbrot set with smooth camera movement and depth-based distortions
// From KarmaViz 67e8a15 warp_maps/fractal/mandelbrot_flythrough.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Camera movement through interesting regions of the Mandelbrot set
    float journey_speed = 0.3;
    float journey_time = t * journey_speed;
    
    // Define a path through interesting Mandelbrot regions
    vec2 camera_path;
    float path_segment = mod(journey_time, 10.0);
    
    if (path_segment < 1.0) {
        // Journey 1: Main bulb to seahorse valley
        float s = path_segment;
        camera_path = mix(vec2(-0.5, 0.0), vec2(-0.7269, 0.1889), s);
    } else if (path_segment < 2.0) {
        // Journey 2: Seahorse valley to spiral region
        float s = path_segment - 1.0;
        camera_path = mix(vec2(-0.7269, 0.1889), vec2(-0.8, 0.156), s);
    } else if (path_segment < 3.0) {
        // Journey 3: Spiral region to mini-mandelbrot
        float s = path_segment - 2.0;
        camera_path = mix(vec2(-0.8, 0.156), vec2(-1.25066, 0.02012), s);
    } else {
        // Journey 4: Mini-mandelbrot back to main bulb
        float s = path_segment - 3.0;
        camera_path = mix(vec2(-1.25066, 0.02012), vec2(-0.5, 0.0), s);
    }
    
    // Dynamic zoom level
    float zoom_cycle = sin(journey_time * 0.5) * 0.5 + 0.1;
    float zoom = 0.5 + zoom_cycle * 2.0; // Zoom from 0.5x to 2.5x
    
    // Transform coordinates to fractal space
    vec2 fractal_pos = (pos - 0.5) / zoom + camera_path;
    
    // Mandelbrot iteration with escape time
    vec2 z = vec2(0.0);
    vec2 c = fractal_pos;
    float iterations = 0.0;
    float max_iterations = 12.0;
    
    // Track orbit for additional effects
    float orbit_trap = 1000.0;
    vec2 orbit_center = vec2(0.0, 0.0);
    
    for (float i = 0.0; i < max_iterations; i++) {
        if (dot(z, z) > 4.0) break;
        
        // Track closest approach to orbit center
        float dist_to_center = distance(z, orbit_center);
        orbit_trap = min(orbit_trap, dist_to_center);
        
        // Mandelbrot iteration: z = z^2 + c
        z = vec2(
            z.x * z.x - z.y * z.y + c.x,
            2.0 * z.x * z.y + c.y
        );
        
        iterations += 1.0;
    }
    
    // Calculate escape velocity for smooth coloring
    float escape_value = iterations;
    if (iterations < max_iterations) {
        escape_value += 1.0 - log2(log2(dot(z, z)));
    }
    escape_value /= max_iterations;
    
    // Create depth-based distortion
    float depth = 1.0 - escape_value;
    
    // Generate flow field based on fractal structure
    vec2 flow_direction;
    
    if (escape_value < 0.1) {
        // Inside or near the set - create inward spiral
        vec2 to_center = camera_path - fractal_pos;
        float angle = atan(to_center.y, to_center.x) + t * 2.0;
        flow_direction = vec2(cos(angle), sin(angle)) * depth;
    } else {
        // Outside the set - create escape flow
        float escape_angle = escape_value * 6.28318 + t;
        flow_direction = vec2(cos(escape_angle), sin(escape_angle));
        
        // Modulate by orbit trap for interesting patterns
        float orbit_influence = exp(-orbit_trap * 3.0);
        flow_direction *= (1.0 + orbit_influence * 2.0);
    }
    
    // Add turbulence based on fractal detail
    float detail_level = abs(sin(escape_value * 10.0 + t));
    vec2 turbulence = vec2(
        sin(fractal_pos.x * 8.0 + t * 1.5) * cos(fractal_pos.y * 6.0 + t * 1.2),
        cos(fractal_pos.x * 6.0 + t * 1.3) * sin(fractal_pos.y * 8.0 + t * 1.7)
    ) * detail_level * 0.3;
    
    // Combine effects
    vec2 final_distortion = flow_direction * 0.4 + turbulence;
    
    // Scale distortion based on zoom level (more distortion when zoomed in)
    final_distortion *= (zoom * 0.5 + 0.5);
    
    // Add breathing effect synchronized with the journey
    float breathing = sin(t * 1.5) * 0.1 + 1.0;
    final_distortion *= breathing;
    
    return final_distortion * 0.02;
}
