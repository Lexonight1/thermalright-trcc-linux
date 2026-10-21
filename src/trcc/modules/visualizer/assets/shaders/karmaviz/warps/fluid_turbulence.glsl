// KarmaViz warp map "Fluid Turbulence" (organic): Simulates turbulent fluid motion with swirling patterns
// From KarmaViz 67e8a15 warp_maps/organic/fluid_turbulence.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Turbulence simulation using multiple octaves
    vec2 p = pos * 4.0;
    
    // First octave - large scale turbulence
    float n1 = sin(p.x + t) * cos(p.y + t * 0.8);
    float n2 = cos(p.x * 1.3 + t * 0.9) * sin(p.y * 1.1 + t * 1.1);
    
    // Second octave - medium scale
    p *= 2.0;
    float n3 = sin(p.x + t * 1.5) * cos(p.y + t * 1.3) * 0.5;
    float n4 = cos(p.x * 1.2 + t * 1.4) * sin(p.y * 1.4 + t * 1.6) * 0.5;
    
    // Third octave - fine detail
    p *= 2.0;
    float n5 = sin(p.x + t * 2.0) * cos(p.y + t * 1.8) * 0.25;
    float n6 = cos(p.x * 1.1 + t * 1.9) * sin(p.y * 1.3 + t * 2.1) * 0.25;
    
    // Combine octaves
    vec2 turbulence = vec2(
        n1 + n3 + n5,
        n2 + n4 + n6
    );
    
    // Add curl for swirling motion
    float curl = sin(pos.x * 6.0 + t) * cos(pos.y * 6.0 + t * 0.7);
    turbulence = vec2(
        turbulence.x + curl * 0.3,
        turbulence.y - curl * 0.3
    );
    
    return turbulence * 0.02;
}
