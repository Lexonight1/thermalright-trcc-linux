uniform sampler2D pipes;
uniform sampler2D heat;
uniform float strength;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 pipe = texelFetch(pipes, p, 0);
    vec3 c = pipe.rgb;
    if (pipe.a < 0.5 && strength > 0.0) {        // _fire's palette
        float level = floor(clamp(texelFetch(heat, p / 2, 0).r * 1.4 * strength,
                                  0.0, 1.0) * 255.0) / 255.0;
        c = clamp(vec3(level * 3.0, level * 3.0 - 1.0, level * 3.0 - 2.0),
                  0.0, 1.0);
    }
    FRAG_COLOUR = vec4(c, 1.0);
}
