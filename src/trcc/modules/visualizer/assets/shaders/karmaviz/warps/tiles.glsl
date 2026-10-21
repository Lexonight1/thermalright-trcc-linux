// KarmaViz warp map "tiles" (Mathematical): Simple Tiles
// From KarmaViz 67e8a15 warp_maps/mathematical/tiles.kvwm, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Basi tile pattern
vec2 get_pattern(vec2 pos, float t) {
    return vec2((sin(pos.x *80 + t)) * .005,
              cos(pos.y * 50 - t) * .005
    ) * .10 ;
}
