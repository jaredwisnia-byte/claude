# Sheet-first construction (experimental production line)

This is a **new opt-in** Blender production line. It does not change legacy
`hull`, `atlas`, `sheetset`, `extrude`, `recipe` or their UV baking.

Instead of guessing geometry and UV-unwrapping afterward, it reads *already
painted, dimensioned surface patches* from a JSON manifest and places the
pixels on mathematically defined geometry. The UV rectangle and real-world
dimensions are specified together; the exported GLB retains the atlas UVs.

## Quickstart

Generate two intentionally simple, deterministic demo atlases:

```bash
python long-lines/factory/examples/generate_atlases.py
```

With Blender plus Pillow and the existing factory dependencies available:

```bash
blender -b --factory-startup --python-exit-code 1 -P long-lines/tools/factory.py -- \
  intake_sheetfirst long-lines/factory/examples/sheetfirst_range/manifest.json
blender -b --factory-startup --python-exit-code 1 -P long-lines/tools/factory.py -- \
  intake_sheetfirst long-lines/factory/examples/sheetfirst_curved/manifest.json
blender -b --factory-startup --python-exit-code 1 -P long-lines/tools/factory.py -- \
  build sheetfirst_range_demo sheetfirst_curved_demo
```

Finished assets are in `assets/props/<name>/`:

- `<name>.glb` — Blender GLB with painted atlas embedded in its PBR material.
- `<name>_albedo.png` — the **unchanged input painted atlas** (the geometry
  conforms to its declared pixel regions).
- `<name>_normal.png` — neutral tangent-space normal texture.
- `<name>_orm.png` — constant occlusion=1, roughness and metallic.
- `_preview.png` — rendered front/end/three-quarter contact sheet.
- `_sheetfirst_manifest.json` — source geometry and UV specifications.

The normal and ORM images are **placeholders**; v1 does not estimate true
material microstructure or bake ambient occlusion. Blender GLB vertex normals
reflect the actual curved geometry.

## Manifest format

See `factory/examples/sheetfirst_range/manifest.json` and
`factory/examples/sheetfirst_curved/manifest.json`. Important fields:

```json
{
  "schema": 1,
  "asset_id": "example_name",
  "atlas": {
    "albedo": "painting.png",
    "depth": "signed_depth.png",
    "vector": "vector_displacement.png",
    "size_px": [2048, 1024],
    "roughness": 0.7,
    "metallic": 0.1
  },
  "depth_range_m": {"inward": 0.01, "outward": 0.02},
  "vector_range_m": [0.03, 0.03, 0.03],
  "panels": [
    {
      "name": "front",
      "size_m": [0.76, 0.97],
      "rect_px": [32, 32, 412, 517],
      "origin_m": [-0.38, -0.33, 0],
      "u_axis": [1, 0, 0],
      "v_axis": [0, 0, 1],
      "grid": [20, 24],
      "curve": {"type": "flat"}
    }
  ],
  "seams": [
    {"a": "front", "a_edge": "u1", "b": "right", "b_edge": "u0"}
  ]
}
```

All coordinates and distances use Blender metres, Z up, front toward -Y.
The 2D atlas uses top-left image origin with rectangles `[x0,y0,x1,y1]`
(x1 and y1 are exclusive); u goes left-to-right, v goes bottom-to-top.
The basis `u_axis` and `v_axis` must be orthogonal unit vectors.
Outward normal is `u_axis × v_axis`. The surface is parameterized by its
**actual panel dimensions**, not by a later auto-unwrapper. Rectangles with
more than 5% aspect mismatch fail unless `allow_stretch: true` is explicit.

The panel `curve` can be `{"type":"flat"}` (default) or
`{"type":"cylinder","axis":"u","radius_m":0.95}`. Cylinders use **arc length**:
the specified width or height is measured *along* the curve, so the UV follows
the material naturally. A negative radius bends in the opposite direction.

## Depth and curvature colours

`depth` is optional, pixel-aligned to the albedo and **encoded by convention**:

- Red `#FF0000` = fully recessed by `depth_range_m.inward`.
- White `#FFFFFF` = zero displacement.
- Blue `#0000FF` = fully protruding by `depth_range_m.outward`.

Intermediate colours interpolate. This is signed *normal displacement*. A
full-resolution numerical 16-bit height image would be more precise for a
production extension; v1 reads RGB to match the requested colour vocabulary.

The optional `vector` image provides **local 3D displacement**. Each RGB
channel corresponds to U, V and the **current curved surface normal**.
`#808080` is neutral; each channel maps from 0..255 into -1..+1, multiplied
by the associated `vector_range_m` component. This allows sideways movement
as well as normal relief, including on curved panels.

Each panel has its own `grid` (number of quads, maximum 128 each axis).
High-frequency details need sufficient subdivisions: texture pixels alone do
not create polygonal geometry. Both depth and vector maps are sampled only
*inside their assigned UV rectangles*, and exported UVs are inset half a
texel to avoid bilinear sampling from neighbouring atlas regions.

A `seams` record requires two generated boundaries (including displacement)
to meet within a tolerance (default 3 mm). Undeclared boundaries do not get
stitched automatically.

## What this does and does not fix

- **Fixes:** no automatic UV re-layout, physically dimensioned painted panels,
  aligned depth/curvature data, and deterministic assembly coordinates.
- **Does not solve yet:** watertight topology; hidden/back surfaces; true
  holes and Boolean cuts; thickness, hinges, handles with undersides; organic
  meshes; material generation; reference-image segmentation; complex mesh
  self-intersections. Those require additional part types and connection
  instructions. A panel's painted black window is not automatically a hole.
- **Artwork requirement:** a model-ready UV atlas with known panel rectangles,
  real-world sizes and assembly axes is essential; a perspective photo or
  five-view painting alone is not an authored UV net.

The two examples are synthetic reference art for engineering tests, **not**
the previously generated 1970s oven artwork or a final game-ready model.
The existing factory quality gate still runs after export, but visual
acceptance must be determined by inspecting the PNG previews.

## Automated validation

```bash
python -m unittest discover -s long-lines/tests -p "test_sheetfirst_engine.py" -v
```

GitHub Actions also installs Blender, generates both example atlases, builds
both GLBs and uploads the preview PNGs for inspection. A passing geometry test
is not a guarantee of visual quality.
