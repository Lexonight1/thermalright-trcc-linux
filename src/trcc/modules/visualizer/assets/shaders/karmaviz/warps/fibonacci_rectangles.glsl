// KarmaViz warp map "Fibonacci Rectangles" (geometric): Fibonacci rectangle subdivision with golden ratio spirals in each section
// From KarmaViz 67e8a15 warp_maps/geometric/fibonacci_rectangles.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Fibonacci Rectangle Subdivision
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    
    // Fibonacci sequence: 1, 1, 2, 3, 5, 8, 13, 21...
    float fib[8] = float[](1.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0);
    
    // Normalize position to [0,1]
    vec2 norm_pos = pos;
    
    // Determine which Fibonacci rectangle we're in
    float total_fib = 21.0; // Sum of first 7 Fibonacci numbers
    vec2 displacement = vec2(0.0);
    
    // Create Fibonacci rectangle subdivision
    float current_x = 0.0;
    float current_y = 0.0;
    
    for (int i = 0; i < 16; i++) {
        float fib_ratio = fib[i] / total_fib;
        
        // Rectangle bounds
        vec2 rect_min = vec2(current_x, current_y);
        vec2 rect_max = vec2(current_x + fib_ratio, current_y + fib_ratio);
        
        // Check if position is in this rectangle
        if (norm_pos.x >= rect_min.x && norm_pos.x <= rect_max.x &&
            norm_pos.y >= rect_min.y && norm_pos.y <= rect_max.y) {
            
            // Position within rectangle [0,1]
            vec2 rect_pos = (norm_pos - rect_min) / (rect_max - rect_min);
            
            // Golden ratio spiral within rectangle
            float phi = 1.618033988749;
            float rect_angle = atan(rect_pos.y - 0.5, rect_pos.x - 0.5);
            float rect_radius = length(rect_pos - 0.5);
            
            // Fibonacci spiral displacement
            float spiral_target = 0.1 * pow(phi, rect_angle / 3.14159);
            float spiral_diff = rect_radius - spiral_target;
            
            displacement += normalize(rect_pos - 0.5) * spiral_diff * 0.2;
            
            // Rectangle-specific oscillation
            float rect_oscillation = sin(t * (1.0 + float(i) * 0.5)) * 0.02;
            displacement += vec2(
                cos(float(i) + t) * rect_oscillation,
                sin(float(i) + t * 1.2) * rect_oscillation
            );
            
            break;
        }
        
        // Move to next rectangle position
        current_x += fib_ratio;
        if (current_x >= 1.0) {
            current_x = 0.0;
            current_y += fib_ratio;
        }
    }
    
    // Add overall Fibonacci wave pattern
    float fib_wave = 0.0;
    for (int i = 0; i < 6; i++) {
        fib_wave += sin(norm_pos.x * fib[i] * 2.0 + t) * cos(norm_pos.y * fib[i] * 2.0 + t * 1.1) / fib[i];
    }
    
    displacement += normalize(centered) * fib_wave * 0.05;
    
    return displacement;
}
