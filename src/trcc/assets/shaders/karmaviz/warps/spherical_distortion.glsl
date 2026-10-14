// KarmaViz warp map "Spherical Distortion" (distortion): Maps the image onto a sphere surface with perspective projection
// From KarmaViz 67e8a15 warp_maps/distortion/spherical_distortion.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Spherical Distortion
vec2 get_pattern(vec2 pos, float t) {
    vec2 centered = pos - 0.5;
    float radius = length(centered);

    // Spherical mapping
    float sphere_radius = 0.4 + sin(t * 0.5) * 0.1;

    if (radius < sphere_radius && radius > 0.001) {
        // Calculate height on sphere
        float z = sqrt(sphere_radius * sphere_radius - radius * radius);

        // Project back to 2D with perspective
        float perspective_factor = sphere_radius / (sphere_radius + z * 0.5);
        vec2 spherical = centered * perspective_factor;

        return (spherical - centered) * 0.6;
    }

    return vec2(0.0);
}
