"""Blender half of the FACTORY_V1 interpreter: meshes -> objects -> GLB, and GLB -> QA.

build(): turn v1_geometry meshes into objects with the authored UVs, one material per
semantic class on the original albedo atlas (sRGB; depth and part IDs are never colour
inputs, they only drove geometry), dissolve flat interior grid lines, export a GLB.

verify(): in a *fresh* Blender, import the GLB and measure it: parts present with their
semantic class, closed where required (after welding glTF's UV-seam vertex splits), UVs
equal to the authored ones, texel density, dimensions, real cavity depth by ray cast,
contact between each part and its parent, the atlas embedded. Then render five views.
"""
import json
import math
import os
import random

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

CLASS_SHADING = {   # (roughness, metallic, specular)
    "main_body": (0.62, 0.0, 0.4), "moving_part": (0.5, 0.0, 0.45), "raised_hard": (0.45, 0.05, 0.5),
    "cavity": (0.75, 0.0, 0.3), "glass": (0.08, 0.0, 0.6), "soft": (0.88, 0.0, 0.2),
    "trim": (0.35, 0.35, 0.5), "support": (0.7, 0.0, 0.35),
}


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def class_material(asset_id, cls, image):
    name = "v1_%s_%s" % (asset_id, cls)
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Linear"
    nt.links.new(tex.outputs["Color"], b.inputs["Base Color"])
    r, met, spec = CLASS_SHADING.get(cls, CLASS_SHADING["main_body"])
    b.inputs["Roughness"].default_value = r
    b.inputs["Metallic"].default_value = met
    key = "Specular IOR Level" if "Specular IOR Level" in b.inputs else "Specular"
    b.inputs[key].default_value = spec
    return m


def smooth_by_angle(me, degrees=35.0):
    for p in me.polygons:
        p.use_smooth = True
    if hasattr(me, "use_auto_smooth"):          # Blender <= 4.0
        me.use_auto_smooth = True
        me.auto_smooth_angle = math.radians(degrees)
    elif hasattr(me, "set_sharp_from_angle"):   # Blender 4.1+
        me.set_sharp_from_angle(angle=math.radians(degrees))


def edge_report(me, weld=False):
    bm = bmesh.new()
    bm.from_mesh(me)
    if weld:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    over = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.free()
    return boundary, over


def build(pkg, meshes, report, out_dir, samples_per_part=400):
    """Create the objects and export <asset_id>.glb. Returns the build record."""
    empty_scene()
    asset_id = pkg["asset"]["asset_id"]
    image = bpy.data.images.load(pkg["albedo"], check_existing=True)
    image.colorspace_settings.name = "sRGB"
    rng = random.Random(7)
    record = {"asset_id": asset_id, "glb": os.path.join(out_dir, asset_id + ".glb"), "parts": {},
              "atlas_px": list(image.size), "atlas": os.path.basename(pkg["albedo"])}
    for pid, m in meshes.items():
        info = report["parts"][pid]
        me = bpy.data.meshes.new(pid)
        me.from_pydata(m.verts, [], [list(f) for f in m.faces])
        uv = me.uv_layers.new(name="UVMap")
        for poly, uvs in zip(me.polygons, m.uvs):
            for k, li in enumerate(poly.loop_indices):
                uv.data[li].uv = uvs[k]
        # Dissolve interior grid lines on flat regions; UV islands and creases stay.
        bm = bmesh.new()
        bm.from_mesh(me)
        before = len(bm.faces)
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.05), verts=list(bm.verts),
                                 edges=list(bm.edges), delimit={"UV"})
        bm.to_mesh(me)
        bm.free()
        me.update()
        uv = me.uv_layers.active              # the old layer reference is stale after to_mesh
        smooth_by_angle(me)
        ob = bpy.data.objects.new("%s__%s" % (asset_id, pid), me)
        bpy.context.scene.collection.objects.link(ob)
        ob["factory_part_id"] = pid
        ob["semantic_class"] = info["semantic_class"]
        ob["construction"] = info["construction"]
        ob["closed_required"] = bool(info["closed_required"])
        if info.get("parent"):
            ob["parent_part"] = info["parent"]
        me.materials.append(class_material(asset_id, info["semantic_class"], image))
        boundary, over = edge_report(me)
        loops = [(tuple(me.vertices[l.vertex_index].co), tuple(uv.data[l.index].uv)) for l in me.loops]
        pick = rng.sample(loops, min(samples_per_part, len(loops)))
        record["parts"][pid] = {
            "object": ob.name, "semantic_class": info["semantic_class"], "construction": info["construction"],
            "closed_required": bool(info["closed_required"]), "parent": info.get("parent"),
            "faces_before_dissolve": before, "faces": len(me.polygons),
            "triangles": sum(len(p.vertices) - 2 for p in me.polygons),
            "open_edges_before_export": boundary, "nonmanifold_edges_before_export": over,
            "uv_samples": [[round(c, 6) for c in co] + [round(t, 6) for t in tv] for co, tv in pick]}
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=record["glb"], export_format="GLB", export_extras=True,
                              export_yup=True, export_apply=True, export_texcoords=True,
                              export_normals=True, export_materials="EXPORT")
    record["glb_bytes"] = os.path.getsize(record["glb"])
    return record


# ---------------- verification of the exported file ----------------
def tri_density(me, mw, atlas_px):
    """Area-weighted share of surface whose texel density is within 10% of the median."""
    W, H = atlas_px
    uv = me.uv_layers.active.data
    me.calc_loop_triangles()
    dens, area_sum = [], 0.0
    for t in me.loop_triangles:
        p = [mw @ me.vertices[i].co for i in t.vertices]
        a = ((p[1] - p[0]).cross(p[2] - p[0])).length / 2
        q = [uv[li].uv for li in t.loops]
        au = abs((q[1].x - q[0].x) * (q[2].y - q[0].y) - (q[2].x - q[0].x) * (q[1].y - q[0].y)) / 2 * W * H
        if a < 1e-12:
            continue
        dens.append((math.sqrt(au / a), a))
        area_sum += a
    if not dens:
        return None
    dens.sort()
    acc, med = 0.0, dens[-1][0]
    for d, a in dens:
        acc += a
        if acc >= area_sum / 2:
            med = d
            break
    ok = sum(a for d, a in dens if abs(d / med - 1) <= 0.10) / area_sum if med else 0.0
    return {"median_px_per_m": round(med, 1), "area_share_within_10pct": round(ok, 4)}


def verify(glb, record, core, out_dir, render=True):
    empty_scene()
    bpy.ops.import_scene.gltf(filepath=glb)
    objs = {o.get("factory_part_id"): o for o in bpy.data.objects if o.type == "MESH" and o.get("factory_part_id")}
    res = {"glb": glb, "parts": {}, "failures": []}
    fail = res["failures"].append
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    bvh = {}
    for pid, rec in record["parts"].items():
        ob = objs.get(pid)
        if ob is None:
            fail("part %s missing from the exported GLB" % pid)
            res["parts"][pid] = {"present": False}
            continue
        me, mw = ob.data, ob.matrix_world
        r = {"present": True, "semantic_class": ob.get("semantic_class"),
             "class_kept": ob.get("semantic_class") == rec["semantic_class"],
             "triangles": sum(len(p.vertices) - 2 for p in me.polygons)}
        if not r["class_kept"]:
            fail("part %s lost its semantic class in the GLB" % pid)
        boundary, over = edge_report(me, weld=True)
        r["open_edges"], r["nonmanifold_edges"] = boundary, over
        r["manifold"] = boundary == 0 and over == 0
        if rec["closed_required"] and not r["manifold"]:
            fail("part %s should be closed: %d open / %d over-shared edges after reimport" % (pid, boundary, over))
        # UV round trip: authored (position, uv) samples must exist in the reimported mesh.
        if not me.uv_layers:
            fail("part %s has no UVs after reimport" % pid)
        else:
            uvd = me.uv_layers.active.data
            kd = KDTree(len(me.vertices))
            for v in me.vertices:
                kd.insert(mw @ v.co, v.index)
            kd.balance()
            vloops = {}
            for l in me.loops:
                vloops.setdefault(l.vertex_index, []).append(uvd[l.index].uv)
            hit = 0
            for s in rec["uv_samples"]:
                co, tv = Vector(s[:3]), s[3:]
                found = False
                for _, idx, dist in kd.find_range(co, 2e-5):
                    if any(abs(u.x - tv[0]) < 2e-4 and abs(u.y - tv[1]) < 2e-4 for u in vloops.get(idx, [])):
                        found = True
                        break
                hit += found
            n = len(rec["uv_samples"])
            r["uv_samples_matched"] = "%d/%d" % (hit, n)
            r["uv_roundtrip_ok"] = n > 0 and hit / n >= 0.995
            uvs = [u for us in vloops.values() for u in us]
            r["uv_inside_atlas"] = all(-1e-4 <= u.x <= 1 + 1e-4 and -1e-4 <= u.y <= 1 + 1e-4 for u in uvs)
            if not r["uv_roundtrip_ok"] or not r["uv_inside_atlas"]:
                fail("part %s UVs changed in the GLB round trip (%s matched)" % (pid, r["uv_samples_matched"]))
            r["texel_density"] = tri_density(me, mw, record["atlas_px"])
        imgs = [n.image for s in me.materials if s and s.use_nodes for n in s.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
        r["atlas_embedded"] = any(list(i.size) == record["atlas_px"] for i in imgs)
        if not r["atlas_embedded"]:
            fail("part %s material lost the original %dx%d atlas" % ((pid,) + tuple(record["atlas_px"])))
        cs = [mw @ v.co for v in me.vertices]
        plo = Vector([min(c[i] for c in cs) for i in range(3)])
        phi = Vector([max(c[i] for c in cs) for i in range(3)])
        r["bbox_m"] = [round(x, 4) for x in phi - plo]
        r["bbox_min"] = [round(x, 4) for x in plo]
        lo = Vector([min(lo[i], plo[i]) for i in range(3)])
        hi = Vector([max(hi[i], phi[i]) for i in range(3)])
        bvh[pid] = BVHTree.FromPolygons(cs, [tuple(p.vertices) for p in me.polygons])
        res["parts"][pid] = r
    for pid in core["parts"]:
        if not core["parts"][pid].get("built"):
            fail("part %s was not built" % pid)
    # contact with parent
    for pid, rec in record["parts"].items():
        par = rec.get("parent")
        if not par or pid not in bvh or par not in bvh:
            continue
        ob = objs[pid]
        cs = [ob.matrix_world @ v.co for v in ob.data.vertices]
        gap = min(bvh[par].find_nearest(c)[3] for c in cs)
        touching = gap <= 0.0015 or bool(bvh[pid].overlap(bvh[par]))
        res["parts"][pid]["gap_to_parent_mm"] = round(gap * 1000, 2)
        res["parts"][pid]["touches_parent"] = touching
        if not touching:
            fail("part %s floats %.1f mm from its parent %s" % (pid, gap * 1000, par))
    # cavities: real depth by ray cast against the owner part only
    res["openings"] = []
    for cav in core["cavities"]:
        tree = bvh.get(cav["owner_part"])
        n = Vector(cav["normal"])
        c = Vector(cav["centre_m"])
        out = {"id": cav["id"], "declared_depth_m": cav["depth_m"]}
        if tree is None:
            fail("cavity %s: owner mesh missing" % cav["id"])
        else:
            loc, nrm, _, dist = tree.ray_cast(c + n * 0.05, -n, 1.0)
            measured = None if loc is None else dist - 0.05
            out["measured_depth_m"] = None if measured is None else round(measured, 4)
            if cav["closed_bottom"]:
                out["ok"] = measured is not None and abs(measured - cav["depth_m"]) <= 0.0015
            else:
                out["ok"] = measured is None or measured > cav["depth_m"] + 0.001
            if not out["ok"]:
                fail("cavity %s: measured depth %s m, declared %.4f m" % (cav["id"], out["measured_depth_m"], cav["depth_m"]))
        res["openings"].append(out)
    res["overall_bbox_m"] = [round(x, 4) for x in hi - lo]
    res["overall_min"] = [round(x, 4) for x in lo]
    res["triangles"] = sum(p.get("triangles", 0) for p in res["parts"].values())
    if render:
        res["previews"] = render_views(list(objs.values()), lo, hi, out_dir, record["asset_id"])
    return res


def render_views(objs, lo, hi, out_dir, asset_id, res_px=560):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = res_px
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("v1_studio")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.16, 0.17, 0.19, 1)
    bg.inputs["Strength"].default_value = 0.9
    scene.world = world
    size = hi - lo
    centre = (lo + hi) / 2
    big = max(size)

    def light(name, direction, energy, colour):
        ld = bpy.data.lights.new(name, "SUN")
        ld.energy = energy
        ld.color = colour
        lo_ = bpy.data.objects.new(name, ld)
        scene.collection.objects.link(lo_)
        lo_.rotation_euler = (-Vector(direction)).to_track_quat("-Z", "Y").to_euler()
    light("key", (-0.5, -0.8, 0.6), 3.2, (1.0, 0.93, 0.82))
    light("fill", (0.7, -0.3, 0.3), 1.0, (0.75, 0.84, 1.0))
    light("rim", (0.3, 0.9, 0.5), 1.4, (0.9, 0.95, 1.0))
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    views = {   # name: (direction from centre, ortho extent axes)
        "front": ((0, -1, 0), (0, 2)), "side": ((1, 0, 0), (1, 2)), "rear": ((0, 1, 0), (0, 2)),
        "top": ((0, 0, 1), (0, 1)), "threequarter": ((1, -1.15, 0.75), None)}
    paths = {}
    for name, (d, axes) in views.items():
        d = Vector(d).normalized()
        if axes:
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = 1.18 * max(size[axes[0]], size[axes[1]])
            cam.location = centre + d * (big * 3)
        else:
            cam.data.type = "PERSP"
            cam.data.lens = 50
            cam.location = centre + d * (big * 2.6)
        up = "Y" if name != "top" else "Y"
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", up).to_euler()
        cam.data.clip_end = big * 20
        p = os.path.join(out_dir, "%s_%s.png" % (asset_id, name))
        scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        paths[name] = p
    return paths
