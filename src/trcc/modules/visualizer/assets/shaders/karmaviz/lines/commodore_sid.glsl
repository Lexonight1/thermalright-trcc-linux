// KarmaViz waveform "commodore_sid" (retro): Commodore 64 SID chip emulation
// From KarmaViz 67e8a15 waveforms/retro/commodore_sid.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // SID-style waveform mixing
    float saw = 2.0 * fract(sample * 4.0) - 1.0;
    float pulse = step(0.3, fract(sample * 4.0)) * 2.0 - 1.0;
    float triangle = 2.0 * abs(fract(sample * 4.0) - 0.5) - 0.5;

    // Mix waveforms based on sample intensity
    float mix = abs(sample);
    float sid_wave = mix * saw + (1.0 - mix) * (pulse + triangle) * 0.5;

    float waveform_value = sid_wave * waveform_scale;
    return waveform_value;
}
