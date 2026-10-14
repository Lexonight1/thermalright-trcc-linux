// KarmaViz warp map "Quantum Vortex" (experimental): Modiied spiral pattern iwth swirling kaleidoscopic appearance.
// From KarmaViz 67e8a15 warp_maps/experimental/quantum_vortex.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Basic spiral warp pattern
vec2 get_pattern(vec2 pos, float t) {
    float angle = atan(pos.y - 0.5, pos.x - 0.5);
    float dist = length(pos - 0.5);
    return vec2(sin(angle * 3.0 + t + (dist /t) * 20.0),
                cos(angle * 100.0 + t + dist * 8.0)) * 0.03;
}
