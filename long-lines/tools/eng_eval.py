"""Score an engineering-drawing reading against the real model (test bench for eng_read.py).

    blender -b --factory-startup -P tools/eng_eval.py -- MODEL.glb read.json OUT_DIR

Volume IoU on a 1 cm lattice for the silhouette solid (what hull lines build) and for the
reader's solid; then renders the real model, the silhouette solid and the reading side by side.
"""
import json
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def model_bvh(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    obs = [o for o in bpy.data.objects if o.type == "MESH"]
    trees = []
    for o in obs:
        dg = bpy.context.evaluated_depsgraph_get()
        trees.append(BVHTree.FromObject(o, dg))
    lo = Vector([min((o.matrix_world @ Vector(c))[i] for o in obs for c in o.bound_box) for i in range(3)])
    hi = Vector([max((o.matrix_world @ Vector(c))[i] for o in obs for c in o.bound_box) for i in range(3)])
    return obs, trees, lo, hi


def inside_model(trees, p):
    for t in trees:
        hit = t.find_nearest(p)
        if hit[0] is not None and (hit[0] - p).dot(hit[1]) > 0:
            return True
    return False


def cell_lookup(planes, solid, lo, size, p):
    import bisect
    idx = []
    for k, ax in enumerate("xyz"):
        f = (p[k] - lo[k]) / size[k]
        i = bisect.bisect_right(planes[ax], f) - 1
        if i < 0 or i >= len(planes[ax]) - 1:
            return False
        idx.append(i)
    return bool(solid[idx[0]][idx[1]][idx[2]])


def voxel_mesh(name, planes, solid, lo, size):
    P = {ax: [lo[k] + v * size[k] for v in planes[ax]] for k, ax in enumerate("xyz")}
    nx, ny, nz = len(solid), len(solid[0]), len(solid[0][0])
    S = lambda i, j, k: 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and solid[i][j][k]
    verts, faces, vid = [], [], {}

    def V(i, j, k):
        key = (i, j, k)
        if key not in vid:
            vid[key] = len(verts)
            verts.append((P["x"][i], P["y"][j], P["z"][k]))
        return vid[key]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                if not S(i, j, k):
                    continue
                if not S(i - 1, j, k): faces.append([V(i, j, k), V(i, j, k + 1), V(i, j + 1, k + 1), V(i, j + 1, k)])
                if not S(i + 1, j, k): faces.append([V(i + 1, j, k), V(i + 1, j + 1, k), V(i + 1, j + 1, k + 1), V(i + 1, j, k + 1)])
                if not S(i, j - 1, k): faces.append([V(i, j, k), V(i + 1, j, k), V(i + 1, j, k + 1), V(i, j, k + 1)])
                if not S(i, j + 1, k): faces.append([V(i, j + 1, k), V(i, j + 1, k + 1), V(i + 1, j + 1, k + 1), V(i + 1, j + 1, k)])
                if not S(i, j, k - 1): faces.append([V(i, j, k), V(i, j + 1, k), V(i + 1, j + 1, k), V(i + 1, j, k)])
                if not S(i, j, k + 1): faces.append([V(i, j, k + 1), V(i + 1, j, k + 1), V(i + 1, j + 1, k + 1), V(i, j + 1, k + 1)])
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def render(objs, out, lo, hi, label_offset):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 32
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = 900, 560
    w = bpy.data.worlds.get("w") or bpy.data.worlds.new("w")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.2, 0.21, 0.24, 1)
    sc.world = w
    if not bpy.data.objects.get("key"):
        for name, d, e in (("key", (-0.6, -0.9, 0.7), 3.0), ("fill", (0.8, -0.2, 0.4), 1.0), ("rim", (0.2, 1, 0.6), 1.2)):
            L = bpy.data.lights.new(name, "SUN")
            L.energy = e
            o = bpy.data.objects.new(name, L)
            sc.collection.objects.link(o)
            o.rotation_euler = (-Vector(d)).to_track_quat("-Z", "Y").to_euler()
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        sc.collection.objects.link(cam)
        sc.camera = cam
    cam = sc.camera
    c = (lo + hi) / 2
    big = max(hi - lo)
    cam.location = c + Vector((-0.55, -1.1, 0.6)).normalized() * big * 2.1
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.hide_render = o not in objs
    sc.render.filepath = out
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    glb, rj, out = a[0], a[1], a[2]
    os.makedirs(out, exist_ok=True)
    res = json.load(open(rj))
    obs, trees, lo, hi = model_bvh(glb)
    size = hi - lo
    step = 0.01
    n = [max(1, int(size[k] / step)) for k in range(3)]
    tot = {"silhouette_solid": [0, 0], "reading": [0, 0]}
    for i in range(n[0]):
        for j in range(n[1]):
            for k in range(n[2]):
                p = Vector((lo.x + (i + 0.5) * size.x / n[0], lo.y + (j + 0.5) * size.y / n[1], lo.z + (k + 0.5) * size.z / n[2]))
                gt = inside_model(trees, p)
                for key, sol in (("silhouette_solid", res["silhouette_solid"]), ("reading", res["solid"])):
                    r = cell_lookup(res["planes"], sol, lo, size, p)
                    tot[key][0] += gt and r
                    tot[key][1] += gt or r
    scores = {k: round(v[0] / max(1, v[1]), 4) for k, v in tot.items()}
    json.dump(scores, open(os.path.join(out, "scores.json"), "w"))
    print("ENG_EVAL", json.dumps(scores))
    mat = bpy.data.materials.new("clay")
    mat.diffuse_color = (0.75, 0.72, 0.66, 1)
    hull = voxel_mesh("silhouette_solid", res["planes"], res["silhouette_solid"], lo, size)
    read = voxel_mesh("reading", res["planes"], res["solid"], lo, size)
    for o in (hull, read):
        o.data.materials.append(mat)
    render(obs, os.path.join(out, "model.png"), lo, hi, 0)
    render([hull], os.path.join(out, "silhouette_solid.png"), lo, hi, 0)
    render([read], os.path.join(out, "reading.png"), lo, hi, 0)
