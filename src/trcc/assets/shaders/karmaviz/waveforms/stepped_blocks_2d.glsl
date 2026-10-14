// KarmaViz waveform "stepped_blocks_2d" (digital): 2D stepped blocks/pixels across the entire screen
// From KarmaViz 67e8a15 waveforms/digital/stepped_blocks_2d.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Create 2D grid of blocks
    float block_size = 0.05; // Size of each block
    vec2 block_coord = floor(vec2(x_coord, y_coord) / block_size) * block_size;
    
    // Sample audio data based on block position
    float sample_x = clamp(block_coord.x, 0.0, 1.0);
    float sample = texture(waveform_data, vec2(sample_x, 0.5)).r;
    
    // Create stepped levels (16 levels)
    float stepped_sample = floor(sample * 16.0) / 16.0;
    
    // Use Y coordinate to determine if block should be lit
    float block_height = stepped_sample * waveform_scale;
    float normalized_y = 1.0 - y_coord; // Flip Y so blocks grow upward
    
    // Light up blocks below the waveform height
    if (normalized_y <= block_height) {
        return 1.0;
    }
    
    return 0.0;
}
