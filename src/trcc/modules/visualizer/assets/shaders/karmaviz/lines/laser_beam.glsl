// KarmaViz waveform "laser_beam" (futuristic): Focused laser beam intensity
// From KarmaViz 67e8a15 waveforms/futuristic/laser_beam.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Laser beam focus
    float beam_center = 0.5 + sample * 0.2;
    float beam_width = 0.02 + abs(sample) * 0.01;
    float beam_intensity = exp(-pow(abs(norm_x - beam_center) / beam_width, 2.0));

    // Laser flicker
    float flicker = sin(time * 60.0) * 0.1 + 0.9;

    float waveform_value = beam_intensity * flicker * abs(sample) * waveform_scale;
    return waveform_value;
}
