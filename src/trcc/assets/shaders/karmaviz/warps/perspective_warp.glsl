// KarmaViz warp map "Perspective Warp" (distortion): Creates perspective distortion as if viewing the image from an angle
// From KarmaViz 67e8a15 warp_maps/distortion/perspective_warp.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Perspective Warp
vec2 get_pattern(vec2 pos, float t) {
    // Create perspective transformation matrix
    float perspective_strength = 0.3 + sin(t * 0.6) * 0.2;

    // Apply perspective to Y coordinate based on X position
    float x_factor = (pos.x - 0.5) * 2.0;  // -1 to 1
    float y_perspective = pos.y * (1.0 + perspective_strength * x_factor);

    // Apply perspective to X coordinate based on Y position
    float y_factor = (pos.y - 0.5) * 2.0;  // -1 to 1
    float x_perspective = pos.x * (1.0 + perspective_strength * y_factor * 0.5);

    vec2 warped = vec2(x_perspective, y_perspective);
    return (warped - pos) * 0.3;
}
