"""Generate small *synthetic* FACTORY_V1 map package for validator regression.

This generator makes exactly registered engineering art. It does not perform
AI image generation, model reconstruction or Blender builds.
"""
import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PALETTE = {
    "main_body": (255, 255, 255), "glass": (250, 204, 21),
    "raised_hard": (239, 68, 68), "cavity": (59, 130, 246)
}


def font(size=21):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def generate(folder):
    folder = Path(folder)
    asset = json.loads((folder / "asset.json").read_text(encoding="utf-8"))
    w, h = asset["maps"]["canvas_px"]
    paint = Image.new("RGB", (w, h), (35, 37, 42))
    signed = Image.new("RGB", (w, h), (255, 255, 255))
    parts = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(paint)
    q = ImageDraw.Draw(signed)
    p = ImageDraw.Draw(parts)
    rng = random.Random(1974)
    by = {panel["id"]: panel for panel in asset["panels"]}
    bases = {
        "front": (143, 115, 81), "back": (108, 85, 61),
        "left": (116, 87, 64), "right": (120, 91, 67),
        "top": (114, 87, 63), "bottom": (68, 63, 59),
        "screen_face": (52, 73, 79), "dial_face_1": (89, 85, 78),
        "dial_face_2": (89, 85, 78)
    }
    for panel in asset["panels"]:
        x0, y0, x1, y1 = panel["rect_px"]
        name = panel["id"]
        base = bases[name]
        d.rectangle((x0, y0, x1-1, y1-1), fill=base)
        class_name = ("glass" if name == "screen_face" else
                      "raised_hard" if name.startswith("dial") else "main_body")
        p.rectangle((x0, y0, x1-1, y1-1), fill=PALETTE[class_name])
        # Painterly bands and worn edges, deliberately not smooth gradients.
        for k in range(42 if name in ("front", "back", "left", "right") else 10):
            x = rng.randint(x0+7, x1-15)
            y = rng.randint(y0+7, y1-15)
            shade = tuple(max(0,min(255,c+rng.randrange(-18,19))) for c in base)
            d.line((x,y,x+rng.randint(3,12),y+rng.randint(-3,3)),fill=shade,width=rng.randint(1,3))
        d.rectangle((x0+5,y0+5,x1-6,y1-6),outline=tuple(min(255,c+40) for c in base),width=4)
    front = by["front"]["rect_px"]
    fx0, fy0, fx1, fy1=front
    # The screen is a *real separate assembly part* and a declared 12-mm
    # cavity in the main shell. These masks do not alone create those solids.
    screen=(fx0+32,fy0+72,fx0+385,fy0+351)
    d.rounded_rectangle(screen,radius=24,fill=(54,55,55),outline=(198,174,120),width=12)
    d.rounded_rectangle((screen[0]+18,screen[1]+20,screen[2]-18,screen[3]-20),
                        radius=18,fill=(60,76,81),outline=(25,31,32),width=6)
    q.rounded_rectangle(screen,radius=22,fill=(255,90,90))
    p.rounded_rectangle(screen,radius=22,fill=PALETTE["cavity"])
    d.text((fx1-118,fy0+51),"1974",fill=(219,185,133),font=font(22))
    for j, z in enumerate((fy0+135,fy0+242)):
        cx=fx1-44
        d.ellipse((cx-22,z-22,cx+22,z+22),fill=(54,53,50),outline=(178,157,121),width=5)
        d.line((cx,z,cx+14,z-15),fill=(235,226,203),width=4)
        q.ellipse((cx-23,z-23,cx+23,z+23),fill=(120,120,255))
        p.ellipse((cx-23,z-23,cx+23,z+23),fill=PALETTE["raised_hard"])
    # Individual UV screen panel. It is visibly painterly blue-green glass.
    x0,y0,x1,y1=by["screen_face"]["rect_px"]
    d.ellipse((x0+12,y0+12,x1-12,y1-12),outline=(154,178,177),width=12)
    for j in range(12):
        line=(x0+25,y0+30+j*14,x1-30,y0+16+j*14)
        d.line(line,fill=(65+j//2,87+j//2,88+j//2),width=2)
    # Glass bulge: a dome, blue rising from the rim to the centre of the same UV face.
    # (Filled concentric ellipses, outside in; the earlier outline rings stopped halfway
    # and left a flat white centre, which the V1 builder correctly built as a raised ring.)
    steps=min(x1-x0,y1-y0)//2-16
    for j in range(0,steps):
        light=255-int(round(100*math.sin(math.pi/2*j/steps)))
        q.ellipse((x0+14+j,y0+14+j,x1-14-j,y1-14-j),fill=(light,light,255))
    for label in ("dial_face_1","dial_face_2"):
        x0,y0,x1,y1=by[label]["rect_px"]
        d.ellipse((x0+2,y0+2,x1-3,y1-3),fill=(60,57,52),outline=(185,176,147),width=5)
        q.ellipse((x0+3,y0+3,x1-4,y1-4),fill=(110,110,255))
    # Back/side ventilation is represented in the painted maps.
    for label in ("back","left","right"):
        x0,y0,x1,y1=by[label]["rect_px"]
        for j in range(9):
            y=y0+80+j*18
            d.rounded_rectangle((x0+47,y,x1-47,y+7),radius=3,fill=(55,51,48))
    x0,y0,x1,y1=by["top"]["rect_px"]
    d.rounded_rectangle((x0+30,y0+32,x1-30,y1-32),radius=20,
                        outline=(78,68,59),width=5)
    x0,y0,x1,y1=by["bottom"]["rect_px"]
    d.text((x0+130,y0+139),"AC 120V 60 Hz",fill=(153,140,126),font=font(19))

    # Orthographic reference crops share one real-world scale but occupy a
    # different layout than the UV net. No perspective or 3/4 distortion.
    ortho = Image.new("RGB", tuple(asset["maps"]["reference_canvas_px"]), (29,30,33))
    for face,crop in asset["orthographic_views"].items():
        a=by[face]["rect_px"]
        tile=paint.crop(a)
        sw,sh=crop[2]-crop[0],crop[3]-crop[1]
        if tile.size!=(sw,sh):
            raise AssertionError(face + " physical view dimension mismatch")
        ortho.paste(tile,(crop[0],crop[1]))
    # Synthetic concept is only for test completeness. Not a genuine art
    # direction approval image or rendered 3D model.
    concept=Image.new("RGB",(860,550),(35,38,43))
    c=ImageDraw.Draw(concept)
    c.polygon([(145,130),(540,115),(695,189),(291,210)],fill=(121,94,64))
    c.polygon([(291,210),(695,189),(695,476),(291,490)],fill=(92,68,50))
    c.polygon([(145,130),(291,210),(291,490),(145,411)],fill=(131,104,74))
    c.polygon([(166,160),(273,220),(273,442),(166,393)],fill=(60,78,78))
    c.polygon([(317,231),(625,210),(625,431),(317,455)],fill=(64,71,69))
    c.ellipse((646,240,686,280),fill=(51,48,42),outline=(179,167,142),width=4)
    c.ellipse((646,316,686,356),fill=(51,48,42),outline=(179,167,142),width=4)
    c.text((80,33),"CRT TV 1974 — SYNTHETIC V1 SCHEMA TEST",fill=(239,216,175),font=font(25))
    c.text((85,513),"Not final artwork or a verified 3D render",fill=(222,196,149),font=font(18))
    for name,img in (("albedo.png",paint),("signed_depth.png",signed),
                     ("part_ids.png",parts),("orthographic.png",ortho),
                     ("concept.png",concept)):
        img.save(folder / name, optimize=True)
    print("FACTORY_V1_DEMO_MAPS",str(folder))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",nargs="?",default=str(Path(__file__).resolve().parent))
    a=parser.parse_args()
    generate(a.folder)
