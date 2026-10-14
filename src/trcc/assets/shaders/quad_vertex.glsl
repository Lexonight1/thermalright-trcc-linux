// A quad given in OpenGL's -1..1 already, with its picture coordinates.
in vec2 corner;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(corner, 0.0, 1.0);
}
