#version 330 core

uniform vec4 u_color;
uniform vec4 u_back_color;
uniform sampler2D u_tex;
uniform int u_use_texture;
uniform int u_use_vcolor;
// Uniform opacity of the current draw (1.0 = opaque pass). Translucent
// material runs (SketchUp trans with useTrans) draw last with this < 1.
uniform float u_opacity;
// Per-run diffuse shade for textured faces (colour faces bake it into
// their vertex colours) — 1.0 everywhere else (billboards, previews).
uniform float u_shade;

in vec2 v_uv;
in vec3 v_color;

out vec4 fragColor;

// 4x4 Bayer matrix, thresholds strictly inside (0, 1) so alpha 0 always
// discards and alpha 1 always draws.
const float BAYER[16] = float[16](
     0.5/16.0,  8.5/16.0,  2.5/16.0, 10.5/16.0,
    12.5/16.0,  4.5/16.0, 14.5/16.0,  6.5/16.0,
     3.5/16.0, 11.5/16.0,  1.5/16.0,  9.5/16.0,
    15.5/16.0,  7.5/16.0, 13.5/16.0,  5.5/16.0);

void main() {
    if (u_use_texture == 1) {
        vec4 texel = texture(u_tex, v_uv);
        // Cutout transparency (face-me billboards, leaves, chain-link):
        // discard keeps the depth buffer honest behind the holes. Below the
        // 0.5 cut the test is DITHERED, not hard — mipmap minification
        // averages a sparse cutout's alpha toward its coverage fraction
        // (a chain-link fence reads ~0.12 at distance) and a hard cut would
        // erase it; the Bayer pattern keeps that fraction of pixels, drawn
        // opaque, so distant fences stay visible as a faint weave.
        // Translucent runs (u_opacity < 1) blend instead of cutting.
        if (u_opacity > 0.999 && texel.a < 0.5) {
            int bx = int(mod(gl_FragCoord.x, 4.0));
            int by = int(mod(gl_FragCoord.y, 4.0));
            if (texel.a < BAYER[by * 4 + bx]) discard;
            fragColor = vec4(texel.rgb * u_shade, 1.0);
            return;
        }
        fragColor = vec4(texel.rgb * u_shade, texel.a * u_opacity);
    } else {
        // SketchUp-style face culling colours: front = paper white, back =
        // blue-grey. Orientation is guaranteed outward by the engine, so a
        // visible back face means "you are looking at the inside" (or at a
        // genuinely inverted face).
        // u_use_vcolor: the batched face pass carries its per-face shaded
        // colour as a vertex attribute — ONE draw call for the whole model
        // instead of one per colour run. That pass draws imported REFERENCE
        // groups, whose faces show their own colour on both sides (SketchUp
        // paints each side; thin ironwork would otherwise flash the back
        // tint). The back tint stays on the user's own drawing (u_color
        // path), where it is honest "you are looking at the inside" feedback.
        vec4 front = (u_use_vcolor == 1) ? vec4(v_color, u_opacity) : u_color;
        fragColor = (gl_FrontFacing || u_use_vcolor == 1) ? front
                                                          : u_back_color;
    }
}
