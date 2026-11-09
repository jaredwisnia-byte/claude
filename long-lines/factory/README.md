# The asset factory

The factory is the product; the models are its output. This folder holds the factory's
standards, work orders, ledger and report. The machinery is in `tools/`.

## Flow

```
reference image ─► INTAKE ─► work order ─► LINE (build) ─► BAKE/EXPORT ─► QA GATE ─┬─► shipped
 (REFERENCE_SPEC)   classify,   factory/      one per         propkit.bake    propkit.qa   │   (library.json)
                    split views, orders/      category        GLB + 3 maps    standards    └─► 1 codified
                    size from                                                                  correction,
                    categories.json                                                            then "needs_person"
every step ─► factory/ledger.jsonl ─► factory/REPORT.md (KPIs)
```

## Commands (all run inside Blender's Python)

```bash
F="blender -b --factory-startup -P long-lines/tools/factory.py --"
$F intake sheet.png furniture_chair_kitchen --category chair     # writes the work order + views
$F intake_sheet factory/sheets_appliances_2026_11_08.json --source-dir DIR   # many orders from one sheet spec
$F intake_set fridge_1_manifest.json --category fridge --name fridge_1_set   # a v2 sheet set (depth + part map)
$F build furniture_chair_kitchen                                 # or --pending, --all-orders
$F qa --library                                                  # gate every asset in the library
$F report                                                        # rewrite REPORT.md
blender -b --factory-startup -P long-lines/tools/critique.py -- NAME [VIEWS]   # turnaround, for spot checks
blender -b --factory-startup -P long-lines/tools/match_check.py -- MODEL.glb MANIFEST.json OUT.png   # model depth vs reference depth
```

## Standards (gauges)
- `standards.json`: units, axes, origin, naming, file set, size classes (texture size and
  triangle budget per class) and the **minimum acceptable standard** the gate enforces.
  The gate does not chase perfection: pass and ship, or one automatic correction, then flag.
- `sizing_manual.json` (explained in `SIZING.md`): how big parts and objects should be and why: facts about hands, heights and everyday objects; part depths as formulas over them; where each kind of part can be; believable overall sizes per category. `parts_catalog.json` is generated from it.
- `categories.json`: per category, the production line, the typical real size and line
  options. This is the judgment a person used to make per object.
- `REFERENCE_SPEC.md`: the input gauge, plus a prompt template for the image generator.

## Production lines (specialized machines, shared standards)

| Line | Input | Machine | Good for |
| --- | --- | --- | --- |
| hull | multiview sheet (spec) | `propkit/hull.py`: silhouettes carve a voxel block, painted parts get depth from `part_rules.py` | boxy and solid objects: electronics, appliances, cabinets, crates, suitcases, street boxes |
| car | `car_v1` sheet | `props/cars_painted.py`: one shared body, paint swapped | vehicles of one body family |
| can | label sheet | `props/goods_painted.py` | cylindrical packaging |
| surface | square texture | `tools/make_tiles.py` | floors, walls, carpets, materials |
| recipe | none (code) | `props/*.py` hand recipes | open or thin shapes the hull can't see (bowls, frames, chairs with gaps) and one-off hero props |
| facade | facade atlas sheet (whole elevations) | `propkit/facade.py`: elevation trimmed, storey period by autocorrelation, window lattice recessed only when confident, cornice extruded, sides/roof from the nearest `ext_*` surface | street buildings up to ~10 storeys |
| atlas | parts atlas sheet (panels laid flat) | `propkit/atlas.py`: panels found automatically; the order names front rows / side / top / back / kick / backguard / burners; each front panel is its own bevelled slab; knobs on control panels get depth from `part_rules.py` | appliances, cabinets, any boxy object drawn as parts |
| sheetset | v2 sheet set: painted + depth + part map (+ section) + manifest | `propkit/sheetset.py`: outlines from the part map (protruding parts left out of the carve), body by the hull, every part-map region classified (handle, hinge, foot, seam, vent, pit, dispenser...) and built from its own outline; depth measured side-on, else depth map scaled by the measured parts, clamped and placement-checked by `factory/sizing_manual.json` | any object, best quality; the preferred input from Nov 2026 |
| extrude | trim sheet | `propkit/extrude.py`: mouldings = drawn cross-section (or a default per kind) swept 1.2 m and wrapped with the painted strip; frames = painted front extruded with its opening cut | baseboards, crown, rails, casings, mirror surrounds, access panels |
| sheetfirst | dimensioned UV atlas + optional RGB depth/vector maps + assembly manifest | `propkit/sheetfirst_engine.py` builds flat/curved surface panels directly from authored UV rectangles; `sheetfirst_blender.py` exports with original UVs | opt-in paint-first assembly of appliances, panelled furniture and curved surfaces |

A new line is justified only when a category repeatedly fails another line's gate.

## Codified corrections (the gate's one retry)
- hull: an outline that fails on one view: that view becomes paint-only (dark-on-dark backs).
- hull: holes: convex silhouettes. Over budget: halve the decimation ratio.
Add a rule here and in `factory.py:correct()` whenever a person fixes the same thing twice.

## Condition variants
Every baked model takes `condition` (0 baked .. 1 derelict: faded, grimy, rusted metal),
`snow` and `frost` per placement in layout JSON. One model, many states.

## KPIs (REPORT.md)
Pass rate at the minimum standard, builds passing with no correction, builds needing a
person, mean build time, triangle statistics, failure kinds by frequency, per-line totals.
The ledger records input and output hashes, so the same inputs can be checked to give the
same output.

## Texture manufacturing (legacy lines, Nov 2026)
The following describes legacy production paths only. **The opt-in `sheetfirst`
line instead builds geometry to match a pre-authored UV construction sheet,
preserves the atlas coordinates and skips the legacy bake/unwrap; see
[Sheet-first construction](SHEETFIRST.md).**

The reference sheet is never the texture. Every asset's mesh is unwrapped into its own UV atlas
and the colours are baked into it (`propkit/bake.finish`, padding round islands). What gets baked
is decided by `propkit/texturing.project` for shapes coloured from views (hull, sheet sets):
each surface point blends the views by facing^4 x visibility x resolution, with normals smoothed
so a carved mesh doesn't flip between views, and other assemblies occluding (a handset hides the
base under it). The gate measures UV stretch (share of surface distorted beyond 2x) and texel
density. Geometry comes first: a voxel staircase looks like a texture fault and isn't one, so
sheet sets use the exact polygonal hull (`hull.polygon_hull`): outlines extruded and intersected.

## Sheet specs (multi-asset sheets)
Jared's newer sheets hold many assets each (facade atlases, appliance part atlases, trim
sheets). A sheet spec (`factory/sheets_*.json`) lists one item per asset: the sheet, the
panel numbers or crop box, and the one or two facts the line can't measure (storeys for a
building, a real width when the painting is stylised). Panel numbers come from
`propkit.atlas.panels()`, which is deterministic for a sheet; to label a new sheet, draw its
panels numbered (see `tools/label_panels.py`) and read them off. That is the whole person
step: about one line per asset.

## Scaling to buildings (plan)
Buildings use the same standards with a facade line: establish real dimensions from
storeys (residential 3.0 m, 1960s office 3.7 m floor to floor; a 50-storey tower is about
185 m plus crown), reconstruct massing (base, shaft, setbacks, crown) from the reference,
detect the repeating bay from the facade (period autocorrelation, as `make_tiles.py` does),
model a handful of modules per building (bay, corner, base, setback, crown) at 2-3 levels
of detail, and assemble them by rule. A tall building is many bays, not a stretched box.
