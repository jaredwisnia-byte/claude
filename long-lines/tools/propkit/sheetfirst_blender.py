"""Blender adapter for the UV-first, dimensioned sheet construction engine.

Unlike the legacy bake path this exports *the exact authored UVs* and embeds the
painted atlas without an auto unwrap or image re-projection. Normal/ORM companion
PNGs are neutral placeholders for the v1 surface patches, not baked material maps.
"""
import os
import shutil

import bpy
from PIL import Image

from . import sheetfirst_engine as engine


def build_and_export(manifest_path, asset_name, library_dir):
    spec, paint_path, depth_path = engine.read_manifest(manifest_path)
    atlas = spec["atlas"]
    depth = None
    if depth_path:
        with Image.open(depth_path) as im:
            depth = im.convert("RGB").copy()
    vector = None
    if atlas.get("vector"):
        vector_path = engine._safe_source(os.path.dirname(os.path.realpath(manifest_path)),
                                          atlas["vector"])
        with Image.open(vector_path) as im:
            vector = im.convert("RGB").copy()
    meshes = engine.build_geometry(spec, depth, vector)
    bad_seams = engine.validate_seams(spec, meshes)
    if bad_seams:
        raise ValueError("assembly seams failed: " + "; ".join(bad_seams))

    out_dir = os.path.join(library_dir, asset_name)
    os.makedirs(out_dir, exist_ok=True)
    albedo_file = os.path.join(out_dir, asset_name + "_albedo.png")
    # Blender's image has a real filepath, which is packaged into the exported GLB.
    shutil.copyfile(paint_path, albedo_file)

    # Neutral normal and packed ORM, preserving the existing game's file contract.
    # PBR roughness and metallic are also set directly in the GLB material.
    roughness = float(atlas.get("roughness", .75))
    metallic = float(atlas.get("metallic", .0))
    if not 0 <= roughness <= 1 or not 0 <= metallic <= 1:
        raise ValueError("roughness and metallic must lie in 0..1")
    Image.new("RGB", (4, 4), (128, 128, 255)).save(
        os.path.join(out_dir, asset_name + "_normal.png"))
    Image.new("RGB", (4, 4), (255, round(255 * roughness), round(255 * metallic))).save(
        os.path.join(out_dir, asset_name + "_orm.png"))

    mat = bpy.data.materials.new(asset_name + "_painted_atlas")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    texture = mat.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(albedo_file, check_existing=True)
    texture.image.colorspace_settings.name = "sRGB"
    texture.extension = "CLIP"
    mat.node_tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic

    bpy.ops.object.select_all(action="DESELECT")
    objects = []
    for panel_name, mesh in meshes.items():
        datablock = bpy.data.meshes.new(asset_name + "_" + panel_name)
        datablock.from_pydata(mesh["vertices"], [], mesh["faces"])
        datablock.update()
        layer = datablock.uv_layers.new(name="UVMap")
        for poly in datablock.polygons:
            poly.use_smooth = bool(mesh["curved"])
            for index in poly.loop_indices:
                v_id = datablock.loops[index].vertex_index
                layer.data[index].uv = mesh["uv"][v_id]
        datablock.materials.append(mat)
        obj = bpy.data.objects.new(asset_name + "_" + panel_name, datablock)
        bpy.context.collection.objects.link(obj)
        obj.select_set(True)
        objects.append(obj)
    bpy.context.view_layer.objects.active = objects[0]
    target = os.path.join(out_dir, asset_name + ".glb")
    bpy.ops.export_scene.gltf(
        filepath=target, export_format="GLB", use_selection=True,
        export_yup=True, export_materials="EXPORT", export_texcoords=True,
        export_normals=True)
    with open(manifest_path, "rb") as src, open(
        os.path.join(out_dir, "_sheetfirst_manifest.json"), "wb") as dst:
        dst.write(src.read())

    # Blender Z-up -> GLB Y-up. Compute asset envelope as the old manifest expects.
    vertices = [xyz for m in meshes.values() for xyz in m["vertices"]]
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    tris = sum(2 * len(m["faces"]) for m in meshes.values())
    return {
        "size": [round(hi[0] - lo[0], 4), round(hi[2] - lo[2], 4),
                 round(hi[1] - lo[1], 4)],
        "min": [round(lo[0], 4), round(lo[2], 4), round(-hi[1], 4)],
        "max": [round(hi[0], 4), round(hi[2], 4), round(-lo[1], 4)],
        "tris": tris, "tex": atlas["size_px"][0],
        "uv_source": "sheetfirst", "uv_preserved": True,
        "panels": list(meshes), "atlas_file": os.path.basename(albedo_file),
        "standardized": ["source-authored UV mapping retained (no automatic unwrap)"],
    }


def verify_imported_uvs(asset_name, spec):
    """Inspect meshes currently imported by qa.check, after GLB round-trip.

    Authoring rectangles must survive GLB export/import to within 1 texture pixel.
    This is intentionally stricter than the general texture-density QA gate.
    """
    aw, ah = spec["atlas"]["size_px"]
    errors = []
    metrics = {}
    imported = [o for o in bpy.data.objects if o.type == "MESH"]
    for panel in spec["panels"]:
        name = asset_name + "_" + panel["name"]
        candidates = [obj for obj in imported if obj.name == name or
                      obj.name.startswith(name + ".")]
        if len(candidates) != 1:
            errors.append("expected exactly one exported mesh for " + name)
            continue
        mesh = candidates[0].data
        layer = mesh.uv_layers.active
        if not layer or not layer.data:
            errors.append(name + " has no imported UV coordinates")
            continue
        uv = [d.uv for d in layer.data]
        u0, v0 = min(t.x for t in uv), min(t.y for t in uv)
        u1, v1 = max(t.x for t in uv), max(t.y for t in uv)
        x0, y0, x1, y1 = panel["rect_px"]
        wanted = ((x0 + .5) / aw, 1 - (y1 - .5) / ah,
                  (x1 - .5) / aw, 1 - (y0 + .5) / ah)
        difference = max(abs((got - exp) * scale)
                         for got, exp, scale in zip((u0, v0, u1, v1), wanted,
                                                    (aw, ah, aw, ah)))
        metrics[panel["name"]] = round(difference, 4)
        if difference > 1.0:
            errors.append("%s UV bounds shifted %.2f pixels on GLB round-trip" %
                          (name, difference))
    return errors, metrics
