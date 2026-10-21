// KarmaViz warp map "Newton Fractal" (fractal): Newton's method fractal showing convergence basins with dynamic root attraction
// From KarmaViz 67e8a15 warp_maps/fractal/newton_fractal.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Newton's method for z^3 - 1 = 0
    vec2 z = (pos - 0.5) * 4.0;
    
    // Animate the polynomial slightly
    float poly_rotation = t * 0.1;
    float cos_rot = cos(poly_rotation);
    float sin_rot = sin(poly_rotation);
    z = vec2(
        z.x * cos_rot - z.y * sin_rot,
        z.x * sin_rot + z.y * cos_rot
    );
    
    float iterations = 0.0;
    float max_iterations = 8.0;
    vec2 root_found = vec2(0.0);
    
    // Newton iteration: z = z - f(z)/f'(z)
    // For f(z) = z^3 - 1, f'(z) = 3z^2
    for (float i = 0.0; i < max_iterations; i++) {
        // Calculate z^2
        vec2 z2 = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y);
        
        // Calculate z^3
        vec2 z3 = vec2(
            z2.x * z.x - z2.y * z.y,
            z2.x * z.y + z2.y * z.x
        );
        
        // f(z) = z^3 - 1
        vec2 f = vec2(z3.x - 1.0, z3.y);
        
        // f'(z) = 3z^2
        vec2 fp = vec2(3.0 * z2.x, 3.0 * z2.y);
        
        // Newton step: z = z - f(z)/f'(z)
        // Division: (a+bi)/(c+di) = ((ac+bd) + (bc-ad)i)/(c^2+d^2)
        float denom = fp.x * fp.x + fp.y * fp.y;
        if (denom < 0.001) break;
        
        vec2 quotient = vec2(
            (f.x * fp.x + f.y * fp.y) / denom,
            (f.y * fp.x - f.x * fp.y) / denom
        );
        
        z = z - quotient;
        iterations += 1.0;
        
        // Check convergence to roots
        if (length(f) < 0.01) {
            root_found = z;
            break;
        }
    }
    
    // Determine which root we converged to
    vec2 root1 = vec2(1.0, 0.0);
    vec2 root2 = vec2(-0.5, 0.866);
    vec2 root3 = vec2(-0.5, -0.866);
    
    float dist1 = distance(z, root1);
    float dist2 = distance(z, root2);
    float dist3 = distance(z, root3);
    
    // Create flow toward the nearest root
    vec2 flow_direction;
    if (dist1 < dist2 && dist1 < dist3) {
        flow_direction = normalize(root1 - (pos - 0.5) * 4.0);
    } else if (dist2 < dist3) {
        flow_direction = normalize(root2 - (pos - 0.5) * 4.0);
    } else {
        flow_direction = normalize(root3 - (pos - 0.5) * 4.0);
    }
    
    // Modulate by convergence speed
    float convergence_factor = 1.0 - (iterations / max_iterations);
    
    // Add swirling motion at basin boundaries
    float boundary_distance = min(min(dist1, dist2), dist3);
    float swirl_strength = exp(-boundary_distance * 5.0);
    vec2 swirl = vec2(-flow_direction.y, flow_direction.x) * swirl_strength * sin(t * 2.0);
    
    return (flow_direction * convergence_factor + swirl) * 0.03;
}
