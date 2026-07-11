#version 330 core

uniform vec4 u_color;
uniform vec4 u_back_color;
uniform sampler2D u_tex;
uniform int u_use_texture;

in vec2 v_uv;

out vec4 fragColor;

void main() {
    if (u_use_texture == 1) {
        vec4 texel = texture(u_tex, v_uv);
        // Cutout transparency (face-me billboards, future leaf textures):
        // discard keeps the depth buffer honest behind the holes.
        if (texel.a < 0.5) discard;
        fragColor = texel;
    } else {
        // SketchUp-style face culling colours: front = paper white, back =
        // blue-grey. Orientation is guaranteed outward by the engine, so a
        // visible back face means "you are looking at the inside" (or at a
        // genuinely inverted face).
        fragColor = gl_FrontFacing ? u_color : u_back_color;
    }
}
