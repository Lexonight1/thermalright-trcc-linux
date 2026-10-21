in vec2 v_uv;
uniform sampler2D picture;
void main() {
    FRAG_COLOUR = texture(picture, v_uv);
}
