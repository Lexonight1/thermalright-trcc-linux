// KarmaViz's frame shader: the last frame, warped, mirrored and
// faded, with the waveform drawn on it.  The placeholders are filled
// by adapters/visual/karmaviz.py.
// From KarmaViz 67e8a15 shaders/shaders.py, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.  Changed: no mouse, no click
// effects (shockwaves, ripples); v_uv in, FRAG_COLOUR out.
uniform sampler2D texture0;
uniform float time;
uniform float animation_speed;  // Unified animation speed control

// Waveform rendering uniforms
uniform sampler2D waveform_data;  // 2D texture containing waveform samples (height=1)
uniform sampler2D fft_data;      // 2D texture containing FFT frequency data (height=1)
uniform int waveform_length;     // Number of samples in waveform
uniform float waveform_scale;    // Scale factor for waveform amplitude
uniform bool waveform_enabled;   // Whether to render waveform
uniform vec3 waveform_color;     // Current waveform color from palette
uniform float waveform_alpha;    // Alpha for waveform fade-in effect
uniform float rotation;
uniform float trail_intensity;
uniform float glow_intensity;
uniform float glow_radius;
uniform int symmetry_mode;
// Warp map uniforms
uniform float warp_intensity;
uniform int active_warp_map;
uniform int kaleidoscope_sections;  // Number of sections for kaleidoscope mode
uniform float smoke_intensity;  // Controls smoke/diffusion effect
uniform float pulse_scale;  // Added uniform for pulse scale
uniform vec2 resolution;  // Add resolution uniform
uniform bool warp_first;     // NEW: Toggle for warp vs symmetry order
uniform bool bounce_enabled; // Toggle for bounce effect
uniform float bounce_height; // Height of bounce effect
in vec2 v_uv;

vec2 apply_zoom(vec2 pos, float zoom_amount) {
    vec2 center = vec2(0.5, 0.5);

    return center + (pos - center) * (1.0 / zoom_amount); // Fixed spacing around division operator
}

float get_pulse(float t, float amp) {
    float base_pulse = sin(t * 2.0) * 0.5 + 0.5;
    float amp_pulse = pow(amp, 2.0) * 0.5;
    return 1.0 + (base_pulse * 0.1 + amp_pulse * 0.3) * 0.2; // Fixed trailing zero
}

vec2 apply_symmetry(vec2 pos, float t, int mode) {
    vec2 local_pos = pos;
    vec2 centered = pos - 0.5;  // Define centered coordinates for all modes

    if (mode == 1) {  // Mirror
        return vec2(abs(centered.x), centered.y) + 0.5;
    }
    else if (mode == 2) {  // Quad mirror
        return vec2(abs(centered.x), abs(centered.y)) + 0.5;
    }
    else if (mode == 3) {  // Kaleidoscope
        float angle = atan(centered.y, centered.x);
        float section_angle = 2.0 * 3.14159 / (float(kaleidoscope_sections));

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Find which section we're in and normalize to first section
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Mirror every other section
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        float dist = length(centered);
        return vec2(
            dist * cos(normalized_angle),
            dist * sin(normalized_angle)
        ) + 0.5;
    }
    else if (mode == 4) {  // Grid symmetry
        // Randomly change grid size based on time
        float base_grid = 5.0;  // Minimum grid size
        float grid_range = 5.0;  // Maximum additional cells
        float grid_size = base_grid + mod(t * 0.5, grid_range);  // Time-based grid size between 5-20 (use t)

        vec2 cell = floor(pos * grid_size) / grid_size;  // Cell coordinates
        vec2 cell_pos = fract(pos * grid_size);  // Position within cell

        // Mirror within each cell
        cell_pos = vec2(
            cell_pos.x > 0.5 ? 1.0 - cell_pos.x : cell_pos.x,
            cell_pos.y > 0.5 ? 1.0 - cell_pos.y : cell_pos.y
        );

        return cell + cell_pos / grid_size;
    }
    else if (mode == 5) {  // Funhouse mirrors
        // Create warped mirror sections like a funhouse

        // Divide screen into mirror sections
        float section_angle = atan(centered.y, centered.x);
        float section_count = 6.0 + sin(t * 0.3) * 2.0; // 4-8 mirror sections
        float section_size = 6.28318 / section_count; // 2*PI / sections
        float section_id = floor(section_angle / section_size);

        // Calculate position within current section
        float local_angle = mod(section_angle, section_size);

        // Apply different warping effects to alternating sections
        if (mod(section_id, 3.0) == 0.0) {
            // Convex mirror effect (fish-eye)
            float radius = length(centered);
            radius = sqrt(radius) * 0.8; // Compress radially
            local_pos = 0.5 + normalize(centered) * radius;
        } else if (mod(section_id, 3.0) == 1.0) {
            // Concave mirror effect (zoom in)
            float radius = length(centered);
            radius = radius * radius * 1.5; // Expand radially
            local_pos = 0.5 + normalize(centered) * min(radius, 0.5);
        } else {
            // Wavy mirror effect
            float wave_freq = 8.0;
            float wave_amp = 0.1;
            vec2 wave_offset = vec2(
                sin(centered.y * wave_freq + t * 2.0) * wave_amp,
                cos(centered.x * wave_freq + t * 2.0) * wave_amp
            );
            local_pos = pos + wave_offset;
        }

        // Ensure coordinates stay within bounds
        return fract(local_pos);
    }
    else if (mode == 6) {  // Hexagonal Symmetry
        // Create 6-fold rotational symmetry like a snowflake
        float angle = atan(centered.y, centered.x);
        float radius = length(centered);

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Create 6 sections (60 degrees each)
        float section_angle = 2.0 * 3.14159 / 6.0;
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Mirror every other section for hexagonal symmetry
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        return vec2(
            radius * cos(normalized_angle),
            radius * sin(normalized_angle)
        ) + 0.5;
    }
    else if (mode == 7) {  // Octagonal Symmetry
        // Create 8-fold rotational symmetry
        float angle = atan(centered.y, centered.x);
        float radius = length(centered);

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Create 8 sections (45 degrees each)
        float section_angle = 2.0 * 3.14159 / 8.0;
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Mirror every other section for octagonal symmetry
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        return vec2(
            radius * cos(normalized_angle),
            radius * sin(normalized_angle)
        ) + 0.5;
    }
    else if (mode == 8) {  // Mandala Symmetry
        // Create complex mandala-like symmetry with multiple reflection axes
        float angle = atan(centered.y, centered.x);
        float radius = length(centered);

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Create 12 sections (30 degrees each) for detailed mandala
        float section_angle = 2.0 * 3.14159 / 12.0;
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Create complex mirroring pattern
        // Mirror every other section
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        // Add additional radial mirroring for mandala effect
        float radial_sections = 3.0;
        float radial_section = floor(radius * radial_sections);
        if (mod(radial_section, 2.0) >= 1.0) {
            // Flip the angle for alternating radial bands
            normalized_angle = section_angle - normalized_angle;
        }

        return vec2(
            radius * cos(normalized_angle),
            radius * sin(normalized_angle)
        ) + 0.5;
    }

    else if (mode == 9) {  // Pentagonal Symmetry
        // Create 5-fold rotational symmetry like a pentagon or starfish
        float angle = atan(centered.y, centered.x);
        float radius = length(centered);

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Create 5 sections (72 degrees each)
        float section_angle = 2.0 * 3.14159 / 5.0;
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Mirror every other section for pentagonal symmetry
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        return vec2(
            radius * cos(normalized_angle),
            radius * sin(normalized_angle)
        ) + 0.5;
    }

    else if (mode == 10) {  // Triangular Symmetry
        // Create 3-fold rotational symmetry like a triangle or triquetra
        float angle = atan(centered.y, centered.x);
        float radius = length(centered);

        // Normalize angle to [0, 2π]
        angle = mod(angle + 2.0 * 3.14159, 2.0 * 3.14159);

        // Create 3 sections (120 degrees each)
        float section_angle = 2.0 * 3.14159 / 3.0;
        float section = floor(angle / section_angle);
        float normalized_angle = mod(angle, section_angle);

        // Mirror every other section for triangular symmetry
        if (mod(section, 2.0) >= 1.0) {
            normalized_angle = section_angle - normalized_angle;
        }

        return vec2(
            radius * cos(normalized_angle),
            radius * sin(normalized_angle)
        ) + 0.5;
    }

    return pos;  // No symmetry (mode 0)

}

// WAVEFORM_RENDER_PLACEHOLDER

// Function to render waveform glow effect
float waveform_glow(vec2 pos, float waveform_y, float line_width, float glow_width) {
    float distance_to_line = abs(pos.y - waveform_y);

    // Core line
    float core = 1.0 - smoothstep(0.0, line_width, distance_to_line);

    // Glow effect
    float glow = 1.0 - smoothstep(line_width, glow_width, distance_to_line);
    glow = pow(glow, 1.5) * 0.3;  // Softer glow falloff

    return core + glow * 2;
}

// WARP_MAP_FUNCTIONS_PLACEHOLDER

vec2 apply_warp(vec2 pos, float t, int warp_index) {
    // WARP_DISPATCHER_PLACEHOLDER
    return vec2(0.0); // Default: no warp offset
}

void main() {
    // Initialize position from UV coordinates
    vec2 pos = v_uv;

    // Handle pulse scale effect
    // This scales the texture coordinates to create a zoom effect while keeping vertices fixed
    if (pulse_scale != 1.0) {
        // Calculate reciprocal of pulse_scale for UV adjustment
        // pulse_scale > 1 zooms in by making UV range smaller
        // pulse_scale < 1 zooms out by making UV range larger
        float uv_scale = 1.0 / pulse_scale;

        // Calculate offset to keep zoom centered in screen
        // As UV range changes, we need to shift it to maintain center point
        float uv_offset = (1.0 - uv_scale) * 0.5;

        // Apply the scale and offset to position
        pos = pos * uv_scale + uv_offset;
    }

    // Calculate animation time using unified speed control
    float t = time * animation_speed;

    // Apply rotation FIRST so warp maps and waveform operate on rotated coordinates
    // Convert rotation degrees to radians
    float rotation_angle = radians(rotation * 3);
    vec2 center = vec2(0.5, 0.5);

    // Translate to origin, rotate, translate back
    pos -= center;
    pos = vec2(
        pos.x * cos(rotation_angle) - pos.y * sin(rotation_angle),
        pos.x * sin(rotation_angle) + pos.y * cos(rotation_angle)
    );
    pos += center;

    // GPU waveform will be rendered after symmetry transformations

    // Apply warp map transformation to the coordinate space
    // Normalize coordinates to square aspect ratio for consistent warp behavior
    vec2 aspect_corrected_pos = pos;
    float aspect_ratio = resolution.x / resolution.y;
    if (aspect_ratio > 1.0) {
        // Wide screen - compress X
        aspect_corrected_pos.x = (pos.x - 0.5) / aspect_ratio + 0.5;
    } else {
        // Tall screen - compress Y
        aspect_corrected_pos.y = (pos.y - 0.5) * aspect_ratio + 0.5;
    }

    // Initialize output color
    vec4 color = vec4(0.0);

    vec2 warp_offset = apply_warp(aspect_corrected_pos, t, active_warp_map);
    // Transform warp offset back to screen space
    if (aspect_ratio > 1.0) {
        warp_offset.x *= aspect_ratio;
    } else {
        warp_offset.y /= aspect_ratio;
    }

    vec2 warped_pos = pos + warp_offset * warp_intensity;

    vec2 final_sample_pos;

    if (warp_first) {
        // Apply warp first, then symmetry
        final_sample_pos = apply_symmetry(warped_pos, t, symmetry_mode);
    } else {
        // Apply symmetry first, then warp
        vec2 symmetry_pos = apply_symmetry(pos, t, symmetry_mode);

        // Apply aspect ratio correction for symmetry warp
        vec2 symmetry_aspect_corrected_pos = symmetry_pos;
        if (aspect_ratio > 1.0) {
            symmetry_aspect_corrected_pos.x = (symmetry_pos.x - 0.5) / aspect_ratio + 0.5;
        } else {
            symmetry_aspect_corrected_pos.y = (symmetry_pos.y - 0.5) * aspect_ratio + 0.5;
        }

        vec2 symmetry_warp_offset = apply_warp(symmetry_aspect_corrected_pos, t, active_warp_map);

        // Transform warp offset back to screen space
        if (aspect_ratio > 1.0) {
            symmetry_warp_offset.x *= aspect_ratio;
        } else {
            symmetry_warp_offset.y /= aspect_ratio;
        }

        final_sample_pos = symmetry_pos + symmetry_warp_offset * warp_intensity;
    }



    // Apply bounce effect if enabled
    if (bounce_enabled) {
        // Adjust the sample position based on bounce height
        final_sample_pos.y += bounce_height;
    }

    // Calculate trail effect alpha
    // Higher trail_intensity creates longer-lasting trails
    // trail_intensity: 0.0 (short trail, alpha=0.90) to 5.0 (very long trail, alpha~0.999)
    float trail_alpha = 0.90 + (0.02 * trail_intensity);
    trail_alpha = min(trail_alpha, 0.999);

    // Apply smoke diffusion effect if enabled
    if (smoke_intensity > 0.0) {
        // Scale of random noise effect
        float noise_scale = smoke_intensity * 0.02;
        float time_factor = t;

        // Sample 4 times with decreasing offset for smooth trails
        for(int i = 0; i < 4; i++) {
            float blend = float(i) / 4.0;

            // Create smooth random offset using sine waves
            vec2 noise_offset = vec2(
                sin(final_sample_pos.x * 10.0 + time_factor + blend * 6.28) * noise_scale,
                cos(final_sample_pos.y * 10.0 + time_factor + blend * 6.28) * noise_scale
            );

            // Sample texture with combined offsets
            vec2 samplePos = fract(final_sample_pos + noise_offset);
            color += texture(texture0, samplePos) * (0.25 * glow_intensity);
        }
    } else {
        // No smoke effect - just sample with pattern offset
        for(int i = 0; i < 4; i++) {
            float blend = float(i) / 4.0;
            vec2 samplePos = fract(final_sample_pos);
            color += texture(texture0, samplePos) * (0.25 * glow_intensity);
        }
    }

    // Add GPU waveform rendering AFTER symmetry transformations
    if (waveform_enabled) {
        float waveform_intensity = 0.0;
        
        // All waveforms now use the XY-based approach
        waveform_intensity = compute_waveform_intensity_at_xy(final_sample_pos.x, final_sample_pos.y);

        // Scale the waveform contribution for good visibility without blowout
        vec4 waveform_contribution = vec4(waveform_color * waveform_intensity * 0.4 * waveform_alpha, 0.0);

        // Blend waveform with the existing color
        color.rgb += waveform_contribution.rgb;
    }

    // Clamp to prevent white blowout
    color.rgb = clamp(color.rgb, 0.0, 0.97);

    // Output final color with trail alpha
    FRAG_COLOUR = vec4(color.rgb, trail_alpha);
}
