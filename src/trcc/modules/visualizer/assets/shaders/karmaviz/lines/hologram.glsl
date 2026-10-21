// KarmaViz waveform "hologram" (futuristic): Holographic interference pattern waveform
// From KarmaViz 67e8a15 waveforms/futuristic/hologram.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Create holographic interference
    float interference = sin(norm_x * 50.0 + time * 3.0) * 0.1;
    float flicker = sin(time * 8.0) * 0.05 + 0.95;

    float waveform_value = sample * flicker * (1.0 + interference) * waveform_scale;
    return waveform_value;
}
