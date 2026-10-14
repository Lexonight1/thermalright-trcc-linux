// KarmaViz waveform "data_stream" (digital): Binary data stream visualization
// From KarmaViz 67e8a15 waveforms/digital/data_stream.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }

    // Original waveform logic converted to inline
    if (!waveform_enabled || waveform_length <= 0) {
            float waveform_value = 0.0;
        }
    
        float norm_x = clamp(x_coord, 0.0, 1.0);
        float sample = texture(waveform_data, vec2(norm_x, 0.9)).r;
    
        // Data stream effect
        float stream_pos = norm_x * 32.0 + time * 8.0;
        float bit_value = step(0.5, fract(sin(floor(stream_pos) * 12.9898) * 43758.5453));
    
        // Modulate by audio
        float intensity = abs(sample);
        float waveform_value = (bit_value * 2.0 - 1.0) * intensity * waveform_scale;

    // Render as horizontal waveform line
    float waveform_y = 0.5 + waveform_value * 0.25; // Center at 0.5 with amplitude scaling

    // Create a line with thickness and glow
    float line_thickness = 0.02;
    float glow_radius = 0.08;
    float distance_to_line = abs(y_coord - waveform_y);

    // Core line intensity
    float core_intensity = smoothstep(line_thickness, 0.0, distance_to_line);

    // Glow effect
    float glow_intensity = exp(-distance_to_line * distance_to_line / (glow_radius * glow_radius)) * 0.3;

    return clamp(core_intensity + glow_intensity, 0.0, 1.0);
}
