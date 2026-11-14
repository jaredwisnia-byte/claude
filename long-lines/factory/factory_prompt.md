# Long Lines /factory — stable two-stage prompt contract

This is a **versioned instruction** for ChatGPT, Claude or another model
working within the Long Lines project. `FACTORY_V1.md` and the schemas
are authoritative; do not silently reinterpret them.

## User-facing command

`/factory [object description]`

Example: `/factory 1970s CRT television, walnut veneer, worn, rabbit ears`.

## Step A — brief and design

First make a structured design from the request:

- Name/id; approximate historically appropriate width/depth/height.
- A fixed coordinate convention (Blender X width, Y depth, Z up; front -Y).
- Material and weathering notes compatible with stylized painterly Long
  Dark-like survival-game assets, but not derivative copies.
- Enumerate **all physical pieces**, including invisible faces, trim,
  mechanical parts, cavities, glass, rear/bottom panels, and hinges.
- State unknown dimensions as labelled estimates, not measurements.
- Determine the assembly approach: closed box, curved shell, sweep,
  lathe, independent hardware, cavities, joined panels or soft cushions.

Generate **one concept preview** for aesthetic approval if needed. Never
treat the concept/perspective image as a UV texture or dimensioned drawing.

## Step B — manufacture a construction package

**First write `assembly.json` and `asset.json`, then draw the maps.**
The initial output is a plan for a 3D object, not a painting to interpret
later. Reconcile hidden/back/bottom surfaces and physical part connections
before creating the artwork.

Use six orthographic reference views: front, right, back, left, top,
bottom, with identical reference pixel density. Provide explicit crop
rectangles in `asset.json`.

Build a **dimensioned UV net** where each island has known width/height
in metres. Place those exact rectangles in `asset.json` with a single
`pixels_per_metre` and adequate gutters.

Then generate, at exactly the same pixel coordinates and dimensions:

- `albedo.png`: painterly material colour and wear.
- `signed_depth.png`: red recessed, white zero, blue raised; use exactly
  the range and ramp in `map_spec.md`.
- `part_ids.png`: only the exact flat palette colours in
  `map_spec.md`, distinguishing body, mechanisms, cavities and glass.
- `orthographic.png`: separate six-view *reference* sheet.
- `concept.png`: approved visual reference.

**Never generate the depth and part-ID maps as independently composed
image-generator pictures.** They must be calculated or painted on the
same UV rectangles as the albedo, with geometry-driven features
driving all three maps. Prefer Python image operations for technical maps
and the image generator for concept and painterly material art. Place
painted output inside known pixel bounds instead of interpreting an
arbitrary generated collage as a physical UV layout.

## Step C — validation

Call:

```bash
python long-lines/tools/validate_package.py \
  path/to/asset.json --report path/to/package_qa.json
```

On failure, fix **only** the failing stage:

- ratio mismatch -> change UV rectangle, dimension or physical plan
- unknown part colour -> quantize part-ID map to the exact palette
- depth ramp invalid -> regenerate only the signed-depth map
- absent sixth face -> create hidden surface and assembly joins
- geometry topology unclear -> improve assembly plan, not painted style
- map registration disagreement -> redraw maps from one coordinate master

Don't claim a valid 3D model from a passing preflight. Construct in
Blender, validate closed geometry, true holes/parts, UV round-trip,
final dimensions, and render front/side/back/three-quarter PNGs.

## Step D — deliver

Deliver a ZIP containing everything that exists. Keep filenames exact.
Provide a concise summary distinguishing finished stages from
unvalidated stages. If the Blender model could not be built or checked,
**say that explicitly**; never describe a source image as a finished
model.

## Economics

Cache working stages; do not regenerate successful art because only
mesh assembly failed. Run Blender in local/headless mode and reserve
the language model for planning, decisions, exceptions and quality
review rather than per-object manual scripting.
