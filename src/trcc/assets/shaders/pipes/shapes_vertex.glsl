in vec2 corner;
in vec3 start;
in vec3 finish;
in vec4 shape;
in float material;
uniform vec2 canvas;
out vec3 v_start;
out vec3 v_finish;
out vec4 v_shape;
flat out int v_material;
void main() {
    v_start = start;
    v_finish = finish;
    v_shape = shape;
    v_material = int(material + 0.5);
    // A screen pixel's centre, top row first, to OpenGL's -1..1, bottom first.
    gl_Position = vec4((corner.x + 0.5) / canvas.x * 2.0 - 1.0,
                       1.0 - (corner.y + 0.5) / canvas.y * 2.0, 0.0, 1.0);
}
