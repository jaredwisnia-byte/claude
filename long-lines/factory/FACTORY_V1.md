# Long Lines — FACTORY_V1

**Status: specification + preflight validator, not a new model-generation engine.**

Treat `/factory [description]` in the Long Lines ChatGPT project as a request
to prepare this package. Treat a package as complete only when its contents
satisfy `tools/validate_package.py`.

The controlling source of truth is **a physically dimensioned UV construction
layout**. Paint and geometry share the same layout; the modeller must not
replace it with a fresh automatic unwrap. This standard complements, but
does not replace, the current legacy `hull`, `atlas`, `sheetset`, `extrude`,
or experimental `sheetfirst` builders.

## Deterministic stages

| Stage | Input | Output | When to regenerate |
| --- | --- | --- | --- |
| 0 brief | User's `/factory` request | `asset.json` with measured dimensions and part inventory | Only if requirements change |
| 1 concept | brief | `concept.png` visual approval image | Only if visual art direction fails |
| 2 topology planning | approved brief/concept | `assembly.json`: solids, joins, cavities, axes, separate parts | If topology fails, *not* to fix paint |
| 3 UV plan | assembly + dimensions | panel rectangles in `asset.json` | If panel proportions/gutters are wrong |
| 4 painting | approved UV plan | `albedo.png` exactly following UV rectangles | If aesthetics or materials fail |
| 5 technical maps | same UV pixel grid | `signed_depth.png` + `part_ids.png` | Only affected map on failure |
| 6 orthographic | approved object, same physical dimensions | `orthographic.png` with six view crops | If views are inconsistent |
| 7 preflight | `asset.json`, `assembly.json`, maps | validator JSON report | Whenever inputs change |
| 8 Blender assembly | validated package | textured GLB + logs | If geometry or export fails |
| 9 asset QA | *exported* GLB | manifold, seams, actual UV metrics, PNG renders | If build changes |

**Do not skip assembly planning.** The five-object benchmark showed that
sharp paint on open panels produces inferior assets. A picture cannot encode
a hinge, real handle underside, cavity bottom, or closed solid by signed
depth alone. The toaster experiment succeeded with explicit structural
instructions.

## Canonical package directory

```text
asset_id/
  asset.json           # asset/schema geometry and UV contract
  assembly.json        # explicit solids, joins, cavities and attachments
  concept.png          # one approval concept; NOT a texture
  orthographic.png     # six orthographic reference views
  albedo.png           # authored UV painting
  signed_depth.png     # RED inward, WHITE zero, BLUE outward
  part_ids.png         # exact semantic palette, no AA/blur
  profile.png          # optional section drawings; add through source notes
  README.md            # optional human notes
```

Existing standard orders can still use other filenames; FACTORY_V1 packages
have fixed filenames above for mechanical ingestion.

**The six orthographic views are references** and are *not* interchangeable
with the UV albedo atlas. An orthographic elevation includes foreshortened
or occluded parts; a UV net is a manufacturing-style layout of the material
surfaces. Both must share declared real-world dimensions, but they need
not share image coordinates.

All maps in `maps.canvas_px` share a **registered pixel grid**.
The orthographic image has its own `reference_canvas_px` and
`reference_pixel_scale`, with explicitly declared `orthographic_views`
cropping boxes. The map validator checks files/dimensions, not whether
every painted highlight is artistically identical.

## Physical and UV rules

- Metres for geometry and displacement; pixel rectangles
  `[left, top, right, bottom]` with right/bottom exclusive.
- Each UV panel provides width and height in metres, local `u_axis` and
  `v_axis`, and an absolute `origin_m`.
- The chosen `pixels_per_metre` must match **every** UV rectangle.
  No `allow_stretch` escape hatch in V1. If a panel doesn't fit, fix the
  construction plan or regenerate only that panel.
- At least 4 pixels separate distinct UV rectangles.
- Curved panels require a `curve` type and enough mesh subdivisions at build;
  the area scale follows the *arc length*, not projected/chord width.
- Don't confuse relief with holes: a cavity must be declared explicitly
  in `assembly.json`. Red in the map may guide cavity depth but cannot
  uniquely define a true opening.
- At minimum, a `closed_box` part must provide all six faces and all
  12 declared joins. Connecting geometry must be welded/closed **in Blender**.
- Each panel must belong to exactly one assembly part, and each part's
  physical role needs a semantic class.
- Handles, knobs, wheels, hinges, cables and other distinct components
  are independent solids with their own construction rules, not paint-only
  spots on the body.

## Colour maps

See [map_spec.md](map_spec.md) for the exact signed displacement encoding
and part-ID palette.

**The RGB depth map stores signed scalar displacement along each panel's
local normal.** For more complicated geometry, a separate versioned
vector-displacement map can be added in FACTORY_V2; do not silently
reinterpret red/blue as X/Y/Z movement.

## Preflight

Run before invoking Blender:

```bash
python -m pip install Pillow jsonschema
python long-lines/tools/validate_package.py \
  long-lines/factory/examples/factory_v1_crt_tv/asset.json \
  --report /tmp/factory_preflight.json
```

The included demo maps are generated locally using:

```bash
python long-lines/factory/examples/factory_v1_crt_tv/generate_demo_maps.py
```

Preflight fails on missing maps, bad dimensions, image size mismatches,
UV overlap, non-unit basis vectors, unknown part colours, wrong depth
colour encodings, incomplete six-view crops, incomplete `closed_box`
joins, unknown part references, non-matching construction parts, and
invalid cavity footprints.

**A preflight pass is not a validated GLB.** After construction, use Blender
to check watertight geometry, embedded UV coordinates, visible parts, and
final rendered appearance. Do not claim game-ready assets until that
second stage succeeds.

## Cost controls

1. Normalize language to a small text brief *before* requesting any image.
2. Make exactly one concept image, ask for human approval only if a
   different design choice would be costly; avoid repeatedly generating
   concepts in routine production where an approved style is available.
3. Use one dimensioned UV layout as the coordinate master.
4. Derive the technical maps by painting within known UV rectangles, or
   by deterministic code from structured geometric features. Avoid
   independent generative image calls for each map; they drift.
5. Use Pillow/jsonschema preflight before Blender.
6. Cache stage hashes and rerun only stages whose inputs changed.
7. Batch Blender builds on the local machine, not by asking the language
   model to issue per-vertex instructions.
8. Render all final GLBs consistently and reserve AI visual critique for
   exceptions or batch contact sheets.

## Known limitations

- Neither the deterministic preflight validator nor the current sheet-first
  v1 algorithm can infer a complete sofa from arbitrary photos.
- Artificially perfect dimensioned reference views are an **input
  requirement**, not something every image generator reliably produces.
- Signed depth is inherently a single-valued local heightfield. It cannot
  represent overhangs or interiors without semantic part/cavity rules.
- Schema validation verifies the *declared* plan; real GLB tests are still
  needed to verify implementation, manifold geometry, and UV fidelity.
- The minimal CRT demonstration below uses synthetic placeholder art
  solely to test validator logic and layout proportions. It is not a
  finished 1970s TV or substitute for commissioned painterly art.

## Versioning

The `format` is exactly `FACTORY_V1`; breaking changes require a new
format string (and validator). Store the same versioned specification
in GitHub so ChatGPT and Claude Code can reference a shared contract.
See [factory_prompt.md](factory_prompt.md).
