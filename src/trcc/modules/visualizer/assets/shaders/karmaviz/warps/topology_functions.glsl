// KarmaViz warp map "Topology Functions" (mathematical): Klein bottle, torus, hyperbolic geometry, and stereographic projections
// From KarmaViz 67e8a15 warp_maps/mathematical/topology_functions.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Topological Transformations
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);
    float angle = atan(centered.y, centered.x);

    // Klein bottle projection
    float u = angle + t * 0.3;
    float v = radius * 3.14159 + t * 0.2;

    // Klein bottle parametric equations (projected to 2D)
    float klein_x = (2.0 + cos(v/2.0) * sin(u) - sin(v/2.0) * sin(2.0*u)) * cos(v/2.0);
    float klein_y = (2.0 + cos(v/2.0) * sin(u) - sin(v/2.0) * sin(2.0*u)) * sin(v/2.0);
    vec2 klein_displacement = vec2(klein_x, klein_y) * 0.01;

    // Torus transformation
    float R = 0.3;  // Major radius
    float r = 0.1 + sin(t * 0.5) * 0.05;  // Minor radius
    float torus_u = angle;
    float torus_v = radius * 2.0 * 3.14159;

    vec2 torus_pos = vec2(
        (R + r * cos(torus_v)) * cos(torus_u),
        (R + r * cos(torus_v)) * sin(torus_u)
    );
    vec2 torus_displacement = (torus_pos - centered) * 0.1;

    // Hyperbolic geometry (Poincaré disk)
    float hyperbolic_factor = 2.0 / (1.0 + radius * radius);
    vec2 hyperbolic_displacement = centered * (hyperbolic_factor - 1.0) * 0.05;

    // Stereographic projection
    float stereo_factor = 2.0 / (1.0 + radius * radius + sin(t * 0.4));
    vec2 stereo_displacement = centered * (stereo_factor - 1.0) * 0.03;

    return klein_displacement * 0.3 + torus_displacement * 0.4 +
           hyperbolic_displacement * 0.2 + stereo_displacement * 0.1;
}
