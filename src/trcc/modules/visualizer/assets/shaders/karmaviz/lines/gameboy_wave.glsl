// KarmaViz waveform "gameboy_wave" (retro): Nintendo Game Boy wave channel
// From KarmaViz 67e8a15 waveforms/retro/gameboy_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Game Boy 4-bit wave table
    float wave_pos = fract(sample * 8.0);
    float wave_table[16];
    // Simulate wave table with mathematical function
    float gb_wave = sin(wave_pos * 6.28318) * 0.7 + sin(wave_pos * 12.56636) * 0.3 *glow_radius;

    // 4-bit quantization
    float waveform_value = floor(gb_wave * 8.0) / 8.0 * waveform_scale;
    return waveform_value;
}
