"""Import the *exported* toaster GLB into fresh Blender and check geometry.

Checks geometrical manifold after welding duplicate GLTF seam vertices by
position (GLTF itself is allowed separate vertices at material/UV boundaries).
"""
import json
import os
import sys

import bmesh
import bpy

args=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
if len(args)!=1:
    raise SystemExit("Expected glb path")
path=os.path.realpath(args[0])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=path)
objects=[ob for ob in bpy.data.objects if ob.type=="MESH"]
if len(objects)<3:
    raise AssertionError("not all components round-tripped through GLB")
shells=[ob for ob in objects if "_closed_enclosure" in ob.name]
if len(shells)!=1:
    raise AssertionError("missing named closed enclosure: "+repr([o.name for o in objects]))
shell=shells[0]
checked=[]
albedo_found=False
for ob in objects:
    layer=ob.data.uv_layers.active
    if not layer or not layer.data:
        raise AssertionError("exported part lost UVs: "+ob.name)
    coords=[d.uv for d in layer.data]
    if any(not (-1e-4<=t.x<=1.0001 and -1e-4<=t.y<=1.0001) for t in coords):
        raise AssertionError("exported UV escaped authored image: "+ob.name)
    bm=bmesh.new();bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=1e-5)
    edge_count=sum(len(edge.link_faces)!=2 for edge in bm.edges)
    bm.free()
    if edge_count:
        raise AssertionError("open edges after GLB UV-seam weld %s: %d"%(ob.name,edge_count))
    checked.append({"name":ob.name,"triangles":sum(len(p.vertices)-2 for p in ob.data.polygons),
                    "edge_issues":edge_count})
    for material in ob.data.materials:
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type=="TEX_IMAGE" and node.image and tuple(node.image.size)==(1792,1200):
                    albedo_found=True
if not albedo_found:
    raise AssertionError("original painted 1792x1200 atlas missing in GLB material")
result={"pass":True,"glb":os.path.basename(path),"mesh_count":len(objects),
        "all_parts_manifold_after_seam_welding":True,
        "all_parts_have_uvs":True,"original_painted_atlas_embedded":True,
        "meshes":checked}
report=os.path.join(os.path.dirname(path),"glb_validation.json")
with open(report,"w") as f:
    json.dump(result,f,indent=2)
print("TOASTER_GLB_REIMPORT_PASS",json.dumps(result))
