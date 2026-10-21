// KarmaViz waveform "pulsar_beam" (cosmic): Rotating pulsar beam sweep
// From KarmaViz 67e8a15 waveforms/cosmic/pulsar_beam.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Pulsar rotation
    float rotation_phase = time * 2.0;
    float beam_angle = fract(rotation_phase);

    // Beam sweep
    float beam_width = 0.05 + abs(sample) * 0.03;
    float beam_center = beam_angle;
    float beam_intensity = exp(-pow(abs(norm_x - beam_center) / beam_width, 2.0));

    // Add secondary beam (180 degrees out of phase)
    float secondary_center = fract(beam_angle + 0.5);
    beam_intensity += exp(-pow(abs(norm_x - secondary_center) / beam_width, 2.0)) * 0.6;

    // Neutron star magnetic field modulation
    float magnetic_mod = sin(norm_x * 50.0 + time * 10.0) * 0.1 + 0.9;

    float waveform_value = beam_intensity * magnetic_mod * abs(sample) * waveform_scale;
    return waveform_value;
}
