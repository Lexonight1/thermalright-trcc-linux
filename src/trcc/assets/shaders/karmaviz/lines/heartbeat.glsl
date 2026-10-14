// KarmaViz waveform "heartbeat" (organic): Heartbeat-like pulsing waveform
// From KarmaViz 67e8a15 waveforms/organic/heartbeat.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Its shaping lines only: karmaviz/line.glsl draws the line.
float waveform_value_at(float x_coord, float y_coord, float norm_x, float sample) {
    // Create heartbeat pulse
    float beat_time = mod(time * 1.2, 2.0);
    float pulse = exp(-beat_time * 3.0) * sin(beat_time * 20.0);
    pulse += exp(-(beat_time - 0.3) * 8.0) * sin((beat_time - 0.3) * 30.0) * 0.6;
    pulse = max(0.0, pulse) * 0.5 + 0.5;

    float waveform_value = sample * pulse * waveform_scale;
    return waveform_value;
}
