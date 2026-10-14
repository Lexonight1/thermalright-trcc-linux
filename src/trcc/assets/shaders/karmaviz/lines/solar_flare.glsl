// KarmaViz waveform "solar_flare" (cosmic): Solar flare energy burst pattern
// From KarmaViz 67e8a15 waveforms/cosmic/solar_flare.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Solar flare eruption
    float flare_center = 0.3 + sin(time * 0.5) * 0.2;
    float flare_intensity = abs(sample);
    float distance_to_flare = abs(norm_x - flare_center);

    // Magnetic field lines
    float magnetic_field = sin(norm_x * 15.708 + time * 3.0) * 0.2;

    // Plasma ejection
    float plasma = exp(-distance_to_flare * 5.0) * flare_intensity;
    plasma += exp(-distance_to_flare * 2.0) * flare_intensity * 0.3;

    float waveform_value = (plasma + magnetic_field * flare_intensity) * waveform_scale;
    return waveform_value;
}
