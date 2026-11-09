# FACTORY_V1 registered image maps

`albedo.png`, `signed_depth.png`, and `part_ids.png` have **identical
width, height, orientation, panel rectangles and physical meaning**.
No image should be independently zoomed, cropped, rotated or rescaled.

## Signed depth (RGB, 8-bit PNG)

Red is recessed, white is 0, blue is raised. Let `s` range -1..+1:

- `s=-1`: RGB `(255,0,0)`, move inward by the manifest's
  `depth_range_m.inward`.
- `-1<s<0`: RGB `(255, round(255*(1+s)), round(255*(1+s)))`.
- `s=0`: RGB `(255,255,255)`, no displacement.
- `0<s<1`: RGB `(round(255*(1-s)), round(255*(1-s)),255)`.
- `s=+1`: RGB `(0,0,255)`, move outward by
  `depth_range_m.outward`.

A decoder compatible with `sheetfirst_engine._depth_value` computes
`signed_fraction=(blue-red)/255`, then multiplies by the corresponding
inward/outward range. Values represent **metres along the panel's local
surface normal**, never raw X/Y/Z coordinates. Treat white image background
as zero relief. **No image compression, antialiasing or lighting gradients
that move pixels away from this signed colour ramp.**

An RGBA/palette PNG is not a valid technical map, even if visually similar.
If precision becomes insufficient, FACTORY_V2 may introduce 16-bit numerical
depth maps with an RGB display preview.

## Part IDs (exact solid RGB swatches; no antialiasing)

| Class | RGB | Hex | Physical meaning |
| --- | --- | --- | --- |
| background | 0,0,0 | `#000000` | outside material/empty UV gutter |
| main_body | 255,255,255 | `#FFFFFF` | chassis and fixed enclosure |
| moving_part | 34,197,94 | `#22C55E` | doors, lids, hinges, levers |
| raised_hard | 239,68,68 | `#EF4444` | physical handles, knobs, switches |
| cavity | 59,130,246 | `#3B82F6` | hole, vent, recessed slot or depression |
| glass | 250,204,21 | `#FACC15` | optical glass, screens or transparent glazing |
| soft | 249,115,22 | `#F97316` | upholstery, cables, rubber, cushions |
| trim | 168,85,247 | `#A855F7` | metal edging and painted mouldings |
| support | 20,184,166 | `#14B8A6` | feet, brackets, structural stand |

Colours specify **semantic category, not a complete 3D design**. Each
specific independent part must also be named in `assembly.json`. A blue
rectangle is *not by itself* proof that it should become a through-hole.
The assembly describes its depth, liner and closed bottom or true outlet.

Each panel's primary semantic colour should appear in the corresponding
part-map UV rectangle. Masks must be exact flat colours. The imagery on
the albedo may be painterly, but semantic boundaries are always binary.

## Orthographic references

`orthographic.png` is an independent six-view **reference sheet**, not
the UV atlas. The JSON gives six crop rectangles and one reference scale
in pixels/metre. Every view must use that scale and agree with the declared
overall dimensions. A panel with a curved face can have a longer arc
length in the UV net than its projected width in the orthographic reference.

Required view order by name: front, right, back, left, top, bottom.
Arrange the six crops however they fit; the JSON crop boxes, not visual
labels, are authoritative. A contact sheet or perspective drawing isn't
a substitute for true orthographic geometry.

## UV rectangles

Every `panels[]` rectangle is exclusive right/bottom and carries a
physical `size_m`. In FACTORY_V1 the two dimensions must agree with the
single declared `pixels_per_metre` within two pixels. At least 4 px of
gutter separates different panels. UV layout is **fixed**, not packed
again during Blender export.

Images must be kept in sRGB for `albedo` and interpreted as data
(non-colour) for `signed_depth` and `part_ids`.
