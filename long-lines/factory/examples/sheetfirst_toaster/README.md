# Toaster experiment: first dimensioned, assembled UV-net test

## Why this exists

Claude's benchmark [sheetfirst_vs_legacy](../../../docs/benchmarks/sheetfirst_vs_legacy/REPORT.md)
found that v1's open surface patches lost to the old factory on three of
five objects. The armchair/sofa-type object was notably incomplete: there
were no closed arms, end caps or coherent join constraints.

**This test changes the input, not the benchmark score**. We create a
painted, physically dimensioned six-face papercraft net *from scratch*
instead of repurposing five-view source art as an imaginary UV net.

## Source of truth

- Size: **320 mm W × 220 mm D × 198 mm H** (180-mm body + 18-mm feet).
- Every shell pixel is scaled at **1600 pixels per metre**.
- Six orthographic sheets: front, rear, left, right, top, bottom.
- Each view's physical dimensions match the corresponding UV rectangle.
- Same registered coordinates produce albedo, parts IDs and signed depth.
- **White = 0; red = inward by up to 25 mm; blue = outward by up to 14 mm**.
- The top has a small blue raised dome. The top slots are red-coded.
- The manifest explicitly defines which faces meet and the separate
  components: lever, dial, feet and two 38-mm recessed bread-slot cavities.

## Reproduce

```bash
python long-lines/factory/examples/sheetfirst_toaster/generate_toaster_maps.py

blender -b --factory-startup --python-exit-code 1 \
  -P long-lines/tools/toaster_assembly_test.py -- \
  long-lines/factory/examples/sheetfirst_toaster/manifest.json \
  /tmp/toaster_built

blender -b --factory-startup --python-exit-code 1 \
  -P long-lines/tools/toaster_verify_glb.py -- \
  /tmp/toaster_built/toaster_1974_sheetfirst_test.glb
```

GitHub Actions `sheetfirst-toaster-test.yml` installs Blender, generates
the registered images, constructs the toaster, reimports its GLB, verifies
manifold geometry and UV preservation, and uploads the GLB, all maps,
six-view orthographic image and real Blender turnaround in a ZIP.

## Actual Blender verification

Successful end-to-end run:
https://github.com/jaredwisnia-byte/claude/actions/runs/42544389761

The smoke test measured:
- **0 open shell edges** on the source mesh
- **0 open geometry edges on GLB reimport** after welding duplicate UV-seam
  vertices by spatial position (GLTF may duplicate vertices at UV seams)
- **7 watertight component meshes** after GLB roundtrip: shell, lever,
  knob and four feet
- **8,136 total triangles**
- **2 actual recessed top cavities, 38 mm deep**
- **1.1 mm measured raised top surface** at the off-centre probe location
- The original painted **1792 × 1200** atlas is embedded as the GLB
  material and original per-face UVs remain present.

These are engineering results; whether the painting meets the desired
game's art direction is a separate visual judgment.

## Important limitation

**The algorithm does not yet infer assembly from photographs.** The
`assembly` instructions in `manifest.json` explicitly say how to close
the object and position its cavities and hardware. The colour map supplies
surface height, not connectivity or part identity. The `parts` map is
produced for the next stage of development; this experimental build reads
the *assembly manifest*, not the parts image, to construct separate pieces.

The Blender assembler is an experimental `sealed_box_with_cavities`
prototype and is *not yet an object-agnostic construction mode*. It is
intentionally separate from the production `hull`, `atlas`,
`sheetset` and `extrude` paths and does not replace their code.

**The next experiment should be a planner:** given the six painted
orthographic images, the parts map and the depth map, automatically propose
a list of connected panels, holes and physical parts; emit an assembly
manifest; then verify it against views and fix ambiguous attachments.
That is the missing visual/spatial inference the benchmark exposed.
