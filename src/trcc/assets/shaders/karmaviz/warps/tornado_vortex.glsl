// KarmaViz warp map "Tornado Vortex" (motion): Multiple tornado systems with spiral inflow and atmospheric turbulence
// From KarmaViz 67e8a15 warp_maps/motion/tornado_vortex.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
vec2 get_pattern(vec2 pos, float t) {
    // Multiple tornado centers
    vec2 tornado1 = vec2(0.3 + sin(t * 0.2) * 0.2, 0.5);
    vec2 tornado2 = vec2(0.7 + cos(t * 0.15) * 0.15, 0.4 + sin(t * 0.3) * 0.3);
    
    vec2 total_vortex = vec2(0.0);
    
    // Process each tornado
    for (int i = 0; i < 2; i++) {
        vec2 tornado_center = (i == 0) ? tornado1 : tornado2;
        vec2 offset = pos - tornado_center;
        float radius = length(offset);
        
        if (radius > 0.01) {
            // Tangential velocity (stronger near center)
            float vortex_strength = 1.0 / (radius + 0.1);
            vec2 tangent = vec2(-offset.y, offset.x) / radius;
            
            // Inward spiral motion
            vec2 radial = -normalize(offset) * 0.2;
            
            // Vertical motion (updraft/downdraft)
            float vertical_phase = t * 2.0 + radius * 8.0;
            vec2 vertical = vec2(0.0, sin(vertical_phase) * 0.3);
            
            // Combine vortex motions
            vec2 vortex_motion = (tangent * vortex_strength + radial + vertical) * exp(-radius * 2.0);
            
            total_vortex += vortex_motion * ((i == 0) ? 0.6 : 0.4);
        }
    }
    
    // Add atmospheric turbulence
    float turb_x = sin(pos.x * 6.0 + t * 2.0) * cos(pos.y * 4.0 + t * 1.5);
    float turb_y = cos(pos.x * 5.0 + t * 1.8) * sin(pos.y * 7.0 + t * 2.2);
    vec2 turbulence = vec2(turb_x, turb_y) * 0.2;
    
    return (total_vortex + turbulence) * 0.025;
}
