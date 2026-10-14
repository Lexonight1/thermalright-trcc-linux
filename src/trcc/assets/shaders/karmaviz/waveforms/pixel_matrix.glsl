// KarmaViz waveform "pixel_matrix" (digital): Digital pixel matrix display of audio data
// From KarmaViz 67e8a15 waveforms/digital/pixel_matrix.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) return 0.0;
    
    // Pixelate coordinates
    float px = floor(x_coord * 32.0) / 32.0;
    float py = floor(y_coord * 24.0) / 24.0;
    
    // Sample audio based on x position
    float sample = texture(waveform_data, vec2(px, 0.5)).r;
    
    // Create digital matrix effect
    float threshold = abs(py - 0.5) * 2.0;
    return step(threshold, abs(sample)) * sign(sample) * 0.5 + 0.1;
}
float compute_waveform_at_x(float x_coord) { return 0.0; }
