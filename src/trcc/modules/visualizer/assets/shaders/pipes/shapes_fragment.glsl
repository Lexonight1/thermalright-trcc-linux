in vec3 v_start;
in vec3 v_finish;
in vec4 v_shape;
flat in int v_material;
uniform vec2 canvas;
uniform vec3 light;
uniform vec3 halfway;
uniform vec3 ambient[16];
uniform vec3 diffuse[16];
uniform vec3 specular[16];
uniform float shininess[16];
uniform float gain;
uniform float glow;
uniform float far;
void main() {
    // This pixel as the CPU counted it: column, then row from the top.
    vec2 s = vec2(gl_FragCoord.x - 0.5, canvas.y - gl_FragCoord.y - 0.5);
    vec3 n;
    float depth;
    if (v_shape.w > 0.5) {                       // a ball
        vec2 o = (s - v_start.xy) / (v_shape.x + 0.5);
        float d2 = dot(o, o);
        if (d2 > 1.0) discard;
        float nz = sqrt(max(1.0 - d2, 0.0));
        n = vec3(o, nz);
        depth = v_start.z - nz * v_shape.z;
    } else {                                     // a tube
        vec2 d = v_finish.xy - v_start.xy;
        float len = length(d);
        vec2 p = s - v_start.xy;
        float t = dot(p, d) / (len * len);
        float across = (p.y * d.x - p.x * d.y) / len
                       / (v_shape.x + (v_shape.y - v_shape.x) * t);
        if (t < 0.0 || t > 1.0 || abs(across) >= 1.0) discard;
        float nz = sqrt(clamp(1.0 - across * across, 0.0, 1.0));
        n = vec3(-d.y / len * across, d.x / len * across, nz);
        depth = v_start.z + (v_finish.z - v_start.z) * t - nz * v_shape.z;
    }
    int m = v_material;
    float nl = clamp(dot(n, light), 0.0, 1.0);
    float nh = clamp(dot(n, halfway), 0.0, 1.0);
    vec3 c = (ambient[m] + nl * diffuse[m]
              + pow(nh, shininess[m]) * specular[m]) * gain;
    if (glow > 0.0) {                            // _fire.burn
        float shine = (c.r + c.g + c.b) / 3.0 * 0.7 + 0.3;
        c = c * (1.0 - glow) + vec3(1.0, 0.22, 0.05) * shine * glow * 1.25;
    }
    gl_FragDepth = clamp(depth / far, 0.0, 1.0);
    FRAG_COLOUR = vec4(clamp(c, 0.0, 1.0), 1.0);
}
