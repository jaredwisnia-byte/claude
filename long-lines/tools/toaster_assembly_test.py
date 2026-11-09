"""Closed toaster test for sheet-first construction.

Run from repository root:
 blender -b --factory-startup --python-exit-code 1 -P long-lines/tools/toaster_assembly_test.py -- \
   long-lines/factory/examples/sheetfirst_toaster/manifest.json OUTPUT_DIRECTORY

This is an *assembly experiment*, not an object-agnostic image interpreter.
The same authored UV net controls all six physically dimensioned faces.
Slots are actual recessed 3D cavities; the six enclosure panels are welded
into one watertight mesh, not left as six disconnected sheets.
"""
import json
import math
import os
import shutil
import sys

import bmesh
import bpy
from mathutils import Vector
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from propkit import sheetfirst_engine as engine


def font(size=20):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def make(spec, depth, out_dir):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    os.makedirs(out_dir, exist_ok=True)
    source_root = os.path.dirname(os.path.realpath(manifest_path))
    name = spec["asset_id"]
    art = os.path.join(source_root, spec["atlas"]["albedo"])
    texture_target = os.path.join(out_dir, name + "_albedo.png")
    shutil.copy2(art, texture_target)
    for source, ext in ((spec["atlas"]["depth"], "_depth_rgb.png"),
                        (spec["atlas"]["parts"], "_parts.png")):
        shutil.copy2(os.path.join(source_root, source), os.path.join(out_dir, name + ext))
    for source in ("toaster_uv_guide.png","toaster_six_views.png","toaster_depth_legend.png"):
        shutil.copy2(os.path.join(source_root, source), os.path.join(out_dir, source))
    with open(os.path.join(out_dir, name + "_manifest.json"), "w", encoding="utf8") as f:
        json.dump(spec, f, indent=2)

    aw, ah = spec["atlas"]["size_px"]
    panels = {p["name"]: p for p in spec["panels"]}
    dims = spec["dimensions_m"]
    W,D,H,B = dims["width"],dims["depth"],dims["body_height"],dims["body_base_z"]
    TOP = B+H
    cavities = spec["assembly"]["cavities"]
    xs = set(round(-W/2+W*i/32,7) for i in range(33))
    ys = set(round(-D/2+D*j/22,7) for j in range(23))
    for cav in cavities:
        cx,cy = cav["centre_xy_m"]; wx,wy = cav["size_xy_m"]
        xs.update((round(cx-wx/2,7),round(cx+wx/2,7)))
        ys.update((round(cy-wy/2,7),round(cy+wy/2,7)))
    xs,ys = sorted(xs),sorted(ys)
    zs = [B+H*i/18 for i in range(19)]

    vertices=[]; faces=[]; faceuv=[]; vmap={}
    def uv_for(name,fu,fv):
        if name in panels: rect=panels[name]["rect_px"]
        else: rect=spec["component_atlas"][name]
        x0,y0,x1,y1=rect
        return ((x0+.5+fu*(x1-x0-1))/aw,
                1.-(y1-.5-fv*(y1-y0-1))/ah)
    def grid_vertex(name,fu,fv):
        p=panels[name]
        u,v=uv_for(name,fu,fv)
        # Each face's signed colour instructions are sampled in local normal space.
        # Guarantee zero displacement on the outer shell edges to preserve
        # watertight joins between six painted panels.
        if abs(fu)<1e-8 or abs(fu-1)<1e-8 or abs(fv)<1e-8 or abs(fv-1)<1e-8:
            offset=0.
        else:
            x0,y0,x1,y1=p["rect_px"]
            px=x0+.5+fu*(x1-x0-1)
            py=y1-.5-fv*(y1-y0-1)
            sample=engine._sample(depth, px, py)
            r=spec["depth_range_m"]
            offset=engine._depth_value(sample,r["inward"],r["outward"])
        return (engine._position(p,fu,fv,offset), (u,v))

    def face(corners):
        ids=[]
        for co,uv in corners:
            key=tuple(round(float(a),6) for a in co)
            if key not in vmap:
                vmap[key]=len(vertices)
                vertices.append(tuple(map(float,co)))
            ids.append(vmap[key])
        if len(set(ids))<3:
            raise ValueError("degenerate face")
        faces.append(tuple(ids))
        faceuv.append(tuple(c[1] for c in corners))

    def panels_grid(name,uc,vc,omit=None):
        points=[[grid_vertex(name,u,v) for u in uc] for v in vc]
        for j in range(len(vc)-1):
            for i in range(len(uc)-1):
                if omit and omit((uc[i]+uc[i+1])/2,(vc[j]+vc[j+1])/2):
                    continue
                face([points[j][i],points[j][i+1],
                      points[j+1][i+1],points[j+1][i]])

    # Six orthographic physical panels. u/v follow each manifest's oriented axes.
    panels_grid("front", [(x+W/2)/W for x in xs],[(z-B)/H for z in zs])
    panels_grid("back", [(W/2-x)/W for x in reversed(xs)],[(z-B)/H for z in zs])
    panels_grid("right", [(y+D/2)/D for y in ys],[(z-B)/H for z in zs])
    panels_grid("left", [(D/2-y)/D for y in reversed(ys)],[(z-B)/H for z in zs])
    panels_grid("bottom", [(x+W/2)/W for x in xs],[(D/2-y)/D for y in reversed(ys)])

    def inside(x,y,cav):
        cx,cy=cav["centre_xy_m"]; wx,wy=cav["size_xy_m"]
        return abs(x-cx)<wx/2-1e-8 and abs(y-cy)<wy/2-1e-8

    panels_grid("top", [(x+W/2)/W for x in xs],[(y+D/2)/D for y in ys],
                lambda u,v: any(inside(u*W-W/2,v*D-D/2,c) for c in cavities))

    def top_point(x,y):
        return grid_vertex("top",(x+W/2)/W,(y+D/2)/D)
    def extra_point(name,co,u,v):
        return (co,uv_for(name,u,v))
    def wall_quad(a,b,cav,along):
        """An inward facing cavity side, from the dome to the recessed floor."""
        cx,cy=cav["centre_xy_m"]; wx,wy=cav["size_xy_m"]
        floor=TOP-cav["depth_m"]
        t0 = ((a[1]-(cy-wy/2))/wy if along=="y" else
              (a[0]-(cx-wx/2))/wx)
        t1 = ((b[1]-(cy-wy/2))/wy if along=="y" else
              (b[0]-(cx-wx/2))/wx)
        topa=top_point(*a); topb=top_point(*b)
        bottoma=extra_point("slot_wall",(a[0],a[1],floor),t0,0)
        bottomb=extra_point("slot_wall",(b[0],b[1],floor),t1,0)
        # Top boundary UV at z=+depth, darker slot wall tile at floor.
        topa=(topa[0],uv_for("slot_wall",t0,1))
        topb=(topb[0],uv_for("slot_wall",t1,1))
        face([topa,topb,bottomb,bottoma])

    # Slots are not fake painted rectangles: top quads are *omitted*, and each
    # opening is closed by inward-facing cavity walls plus a bottom floor.
    for c in cavities:
        cx,cy=c["centre_xy_m"]; wx,wy=c["size_xy_m"]
        xa,xb=round(cx-wx/2,7),round(cx+wx/2,7)
        ya,yb=round(cy-wy/2,7),round(cy+wy/2,7)
        xs0=[x for x in xs if xa-1e-7<=x<=xb+1e-7]
        ys0=[y for y in ys if ya-1e-7<=y<=yb+1e-7]
        # Inward wall normals: left +X, right -X, front +Y, rear -Y.
        for j in range(len(ys0)-1):
            y0,y1=ys0[j:j+2]
            wall_quad((xa,y1),(xa,y0),c,"y")
            wall_quad((xb,y0),(xb,y1),c,"y")
        for i in range(len(xs0)-1):
            x0,x1=xs0[i:i+2]
            wall_quad((x0,ya),(x1,ya),c,"x")
            wall_quad((x1,yb),(x0,yb),c,"x")
        floor=TOP-c["depth_m"]
        for j in range(len(ys0)-1):
            for i in range(len(xs0)-1):
                x0,x1=xs0[i:i+2];y0,y1=ys0[j:j+2]
                def fp(x,y):
                    u=(x-xa)/wx;v=(y-ya)/wy
                    return extra_point("slot_floor",(x,y,floor),u,v)
                face([fp(x0,y0),fp(x1,y0),fp(x1,y1),fp(x0,y1)])

    data=bpy.data.meshes.new(name+"_watertight_shell")
    data.from_pydata(vertices,[],faces)
    data.update(calc_edges=True)
    uv=data.uv_layers.new(name="UVMap")
    for poly, mapped in zip(data.polygons,faceuv):
        for corner,li in enumerate(poly.loop_indices):
            uv.data[li].uv=mapped[corner]
        poly.use_smooth=False

    shell=bpy.data.objects.new(name+"_closed_enclosure",data)
    bpy.context.collection.objects.link(shell)
    bm=bmesh.new();bm.from_mesh(data)
    open_edges=sum(len(e.link_faces)!=2 for e in bm.edges)
    nonmanifold=sum(len(e.link_faces)>2 for e in bm.edges)
    bm.free()
    if open_edges or nonmanifold:
        raise AssertionError("closed-shell test failed: %d boundary and %d overconnected edges"%(open_edges,nonmanifold))

    # Original atlas is directly connected to all meshes; no UV generation/packing.
    material=bpy.data.materials.new("TOASTER_original_painted_pixels")
    material.use_nodes=True
    nt=material.node_tree
    bsdf=nt.nodes.get("Principled BSDF")
    tex=nt.nodes.new("ShaderNodeTexImage")
    tex.image=bpy.data.images.load(texture_target,check_existing=True)
    tex.image.colorspace_settings.name="sRGB"
    nt.links.new(tex.outputs["Color"],bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value=float(spec["atlas"]["roughness"])
    bsdf.inputs["Metallic"].default_value=float(spec["atlas"]["metallic"])
    shell.data.materials.append(material)

    models=[shell]
    def material_uv(obj,tile,local_xyz):
        bb=spec["component_atlas"][tile]
        uv=obj.data.uv_layers.active or obj.data.uv_layers.new(name="UVMap")
        for poly in obj.data.polygons:
            for li in poly.loop_indices:
                coord=obj.data.vertices[obj.data.loops[li].vertex_index].co
                u,v=local_xyz(coord)
                uv.data[li].uv=uv_for(tile,max(0,min(1,u)),max(0,min(1,v)))
        obj.data.materials.clear()
        obj.data.materials.append(material)
        models.append(obj)

    for a in spec["assembly"]["attachments"]:
        if a["type"]=="box":
            bpy.ops.mesh.primitive_cube_add(size=1,location=a["centre_m"])
            ob=bpy.context.object
            ob.name=name+"_"+a["id"]
            ob.dimensions=a["size_m"]
            bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
            material_uv(ob,a["texture"],lambda co: (.5+co.x/max(a["size_m"][0],.001),
                                                      .5+co.z/max(a["size_m"][2],.001)))
        elif a["type"]=="cylinder_x":
            bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=a["radius_m"],
                                               depth=a["depth_m"],location=a["centre_m"],
                                               rotation=(0,math.pi/2,0))
            ob=bpy.context.object
            ob.name=name+"_"+a["id"]
            bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
            material_uv(ob,a["texture"],lambda co:(.5+co.y/(2*a["radius_m"]),
                                                    .5+co.z/(2*a["radius_m"])))
        else:
            raise ValueError("unknown attachment type: "+a["type"])
    # Assess all parts, not just the shell; separate hardware can be closed solids.
    all_open=0
    all_tris=0
    for ob in models:
        mb=bmesh.new();mb.from_mesh(ob.data)
        all_open+=sum(len(e.link_faces)!=2 for e in mb.edges)
        mb.free()
        all_tris+=sum(len(p.vertices)-2 for p in ob.data.polygons)
    if all_open:
        raise AssertionError("assembly still contains %d open mesh edges"%all_open)

    source_depth=depth
    sx=round(spec["panels"][4]["rect_px"][0]+256)
    sy=round(spec["panels"][4]["rect_px"][1]+176)
    # Actual top centre doesn't lie within a cavity; sample to left of left slot.
    xprobe=-.125;yprobe=0.
    probe=top_point(xprobe,yprobe)[0][2]
    if probe<TOP+0.0008:
        raise AssertionError("blue-painted top relief did not generate curvature")

    # Export only the model, not render helpers.
    bpy.ops.object.select_all(action="DESELECT")
    for ob in models:ob.select_set(True)
    bpy.context.view_layer.objects.active=shell
    path=os.path.join(out_dir,name+".glb")
    bpy.ops.export_scene.gltf(filepath=path,export_format="GLB",use_selection=True,
                              export_yup=True,export_texcoords=True,export_normals=True)
    neutral_normal=Image.new("RGB",(8,8),(128,128,255))
    neutral_normal.save(os.path.join(out_dir,name+"_normal.png"))
    Image.new("RGB",(8,8),(255,round(255*spec["atlas"]["roughness"]),
                              round(255*spec["atlas"]["metallic"]))).save(
                       os.path.join(out_dir,name+"_orm.png"))

    output={"asset":name,"pass":True,"watertight_shell_open_edges":open_edges,
            "all_parts_open_edges":all_open,"triangles":all_tris,
            "panels":list(panels),"slot_count":len(cavities),
            "shell_vertices":len(data.vertices),"shell_faces":len(data.polygons),
            "original_uv_retained":True,
            "depth_map_used":True,"test_top_relief_m":round(probe-TOP,5),
            "slot_depth_m":cavities[0]["depth_m"],
            "note":"Closed, explicit assembly test; no automatic inference from ordinary photos."}
    report=os.path.join(out_dir,"build_report.json")
    with open(report,"w") as f:json.dump(output,f,indent=2)

    # Studio render six angles from real GLB-derived geometry.
    world=bpy.data.worlds.new("Toaster studio")
    world.use_nodes=True
    world.node_tree.nodes["Background"].inputs["Color"].default_value=(.13,.14,.16,1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value=.8
    scene=bpy.context.scene
    scene.world=world
    scene.render.engine="CYCLES"
    scene.cycles.samples=16
    scene.cycles.use_denoising=False
    scene.render.resolution_x=530
    scene.render.resolution_y=480
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format="PNG"

    floor_mat=bpy.data.materials.new("matte charcoal floor")
    floor_mat.diffuse_color=(.19,.20,.21,1)
    bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.002))
    floor_obj=bpy.context.object
    floor_obj.name="STUDIO_ONLY_FLOOR"
    floor_obj.data.materials.append(floor_mat)
    def add_light(label,location,watts,color):
        ld=bpy.data.lights.new(label,"AREA")
        ld.energy=watts;ld.shape="DISK";ld.size=2
        ld.color=color
        lo=bpy.data.objects.new(label,ld)
        scene.collection.objects.link(lo)
        lo.location=location
        lo.rotation_euler=(Vector((0,0,.10))-lo.location).to_track_quat("-Z","Y").to_euler()
    add_light("warm_key",(-.6,-.85,1.0),70,(1,.91,.77))
    add_light("cool_fill",(.55,.6,.8),55,(.72,.83,1.0))
    cam=bpy.data.objects.new("cam",bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam);scene.camera=cam
    cam.data.type="ORTHO";cam.data.ortho_scale=.48

    specs=[("FRONT",(0,-1,.18)),("RIGHT",(1,-.15,.22)),
           ("REAR",(.1,1,.18)),("TOP",(.15,-.1,1.0)),
           ("THREE-QUARTER",(1,-1,.74)),("OPPOSITE",(-1,-1,.72))]
    board=Image.new("RGB",(1590,1050),(31,33,36))
    bd=ImageDraw.Draw(board)
    for idx,(label,direction) in enumerate(specs):
        cam.location=Vector(direction).normalized()*1.3+Vector((0,0,.115))
        cam.rotation_euler=(Vector((0,0,.115))-cam.location).to_track_quat("-Z","Y").to_euler()
        tmp=os.path.join(out_dir,"_temp_render.png")
        scene.render.filepath=tmp
        bpy.ops.render.render(write_still=True)
        tile=Image.open(tmp).convert("RGB")
        col,row=idx%3,idx//3
        board.paste(tile,(col*530,row*525))
        bd.text((col*530+18,row*525+489),label,font=font(22),fill=(242,227,194))
    os.remove(tmp)
    preview=os.path.join(out_dir,"toaster_real_blender_turnaround.png")
    board.save(preview)
    print("TOASTER_TEST_PASS",json.dumps(output), "PREVIEW",preview)
    return output


if __name__=="__main__":
    args=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    if len(args)!=2:
        raise SystemExit("Usage: blender -b -P tools/toaster_assembly_test.py -- MANIFEST.json OUT_DIR")
    manifest_path=os.path.realpath(args[0]);out_dir=os.path.realpath(args[1])
    spec,albedo,depth_path=engine.read_manifest(manifest_path)
    with Image.open(depth_path) as im:
        depth=im.convert("RGB").copy()
    make(spec,depth,out_dir)
