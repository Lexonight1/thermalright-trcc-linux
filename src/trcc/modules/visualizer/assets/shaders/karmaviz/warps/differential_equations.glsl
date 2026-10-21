// KarmaViz warp map "differential_equations" (Mathematical): Lorenz attractor, Van der Pol oscillator, and pendulum equations creating dynamic flow fields
// From KarmaViz 67e8a15 warp_maps/mathematical/differential_equations.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Differential Equations Visualization
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;

    // Lorenz attractor-inspired flow field
    float sigma = 3.0;
    float rho = 8.0 + sin(t * 0.1) * 5.0;
    float beta = 8.0 / 3.0;

    // Scale position to Lorenz space
    vec3 lorenz_pos = vec3(centered.x * 20.0, centered.y * 20.0, sin(t + length(centered) * 5.0) * 10.0);

    // Lorenz equations (simplified for 2D)
    vec3 lorenz_flow = vec3(
        sigma * (lorenz_pos.y - lorenz_pos.x),
        lorenz_pos.x * (rho - lorenz_pos.z) - lorenz_pos.y,
        lorenz_pos.x * lorenz_pos.y - beta * lorenz_pos.z
    );

    vec2 lorenz_displacement = lorenz_flow.xy * 0.001;

    // Van der Pol oscillator
    float mu = 1.0 + sin(t * 0.2) * 0.5;
    float x = centered.x * 5.0;
    float y = centered.y * 5.0;

    // Van der Pol equations
    float dx_dt = y;
    float dy_dt = mu * (1.0 - x * x) * y - x;

    vec2 vanderpol_displacement = vec2(dx_dt, dy_dt) * 0.002;

    // Pendulum equation with damping
    float g = 9.81;
    float L = 1.0;
    float damping = 0.1 + sin(t * 0.15) * 0.05;
    float theta = atan(centered.y, centered.x);
    float theta_dot = length(centered) * 2.0;

    float theta_ddot = -(g / L) * sin(theta) - damping * theta_dot;
    vec2 pendulum_displacement = vec2(
        cos(theta) * theta_ddot,
        sin(theta) * theta_ddot
    ) * 0.003;

    return lorenz_displacement + vanderpol_displacement + pendulum_displacement;
}
