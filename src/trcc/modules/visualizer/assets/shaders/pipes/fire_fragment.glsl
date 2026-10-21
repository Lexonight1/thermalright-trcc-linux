uniform sampler2D heat;
uniform sampler2D pipes;
uniform sampler2D picture;
uniform vec2 canvas;
uniform ivec2 half_size;
uniform int frame;
uniform int lift;
uniform float bass;
uniform float pipe_heat;
uniform float cooling;
uniform vec4 placed;          // centre x, y; tilt; size -- size 0: none
float flicker(ivec2 q) {
    uint h = uint(q.x) * 73856093u ^ uint(q.y) * 19349663u
             ^ uint(frame) * 83492791u;
    h ^= h >> 13u;
    h *= 1274126177u;
    h ^= h >> 16u;
    return float(h & 65535u) / 65535.0;
}
float seed(ivec2 q) {
    float s = texelFetch(pipes, q * 2, 0).a * pipe_heat;
    if (placed.w > 0.0) {
        vec2 screen = vec2(2.0 * float(q.x) + 1.0,
                           canvas.y - 2.0 * float(q.y) - 1.0);
        vec2 uv = vec2((screen.x - placed.x) / placed.z, screen.y - placed.y)
                  / placed.w + 0.5;
        if (all(greaterThanEqual(uv, vec2(0.0))) && all(lessThanEqual(uv, vec2(1.0))))
            s = max(s, texture(picture, uv).a);
    }
    return s;
}
float heated(ivec2 q) {
    if (q.y < 0) return 0.0;
    q.x = (q.x + half_size.x) % half_size.x;
    float f = flicker(q);
    return max(texelFetch(heat, q, 0).r,
               seed(q) * (0.6 + 0.4 * f) * (0.7 + 0.5 * min(bass, 1.3)));
}
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 below = p - ivec2(0, lift);
    float spread = heated(below) + heated(below - ivec2(1, 0))
                   + heated(below + ivec2(1, 0)) + heated(below - ivec2(0, 1));
    FRAG_COLOUR = vec4(spread / 4.0 * pow(0.955 - 0.03 * flicker(p), cooling),
                       0.0, 0.0, 1.0);
}
