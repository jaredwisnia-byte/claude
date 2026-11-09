"""Create a *registered* six-view toaster net, not an image-to-3D guess.

Same atlas rectangles, same physical proportions and same pixel coordinates for:
painted albedo, signed RGB depth and semantic parts. Reproducible, seed=1974.
For a 3D build run tools/toaster_assembly_test.py under Blender afterward.
"""
import json
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "manifest.json")
RNG = np.random.default_rng(1974)
R = random.Random(1974)

def font(px=20):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return ImageFont.load_default()

def textured_region(im, rect, base, noise=8):
    x0, y0, x1, y1 = rect
    h, w = y1-y0, x1-x0
    grain = RNG.normal(0, noise, (h,w,1))
    broad = RNG.normal(0, noise*1.5, (max(2,h//18), max(2,w//18)))
    low = Image.fromarray(np.uint8(np.clip(128+broad,0,255)), "L")
    low = low.resize((w,h), Image.Resampling.BILINEAR)
    wash = (np.asarray(low).astype(np.float32)-128)[:,:,None]
    channels = np.array(base,dtype=np.float32).reshape(1,1,3)
    a = channels + grain*.63 + wash*.57
    arr = np.uint8(np.clip(a,0,255))
    im.paste(Image.fromarray(arr,"RGB"), (x0,y0))

def painterly_chips(draw, rect, count, palette):
    x0,y0,x1,y1 = rect
    for _ in range(count):
        x=R.randint(x0+7,x1-8); y=R.randint(y0+7,y1-8)
        rw=R.randint(3,25); rh=R.randint(1,6)
        col=R.choice(palette)
        draw.polygon([(x,y),(x+rw,y+R.randint(-3,2)),
                      (x+rw//2,y+rh),(x-2,y+rh)],fill=col)

def edging(draw, rect, dark=(65,60,54), light=(225,219,195)):
    x0,y0,x1,y1=rect
    draw.line([(x0+2,y1-2),(x0+2,y0+2),(x1-2,y0+2)], fill=light,width=6)
    draw.line([(x1-3,y0+2),(x1-3,y1-3),(x0+2,y1-3)], fill=dark,width=7)
    draw.rectangle((x0+11,y0+11,x1-11,y1-11),outline=(125,114,91),width=3)

def tile_uv_part(draw, rect, label, base, f):
    textured_region(f,rect,base)
    edging(draw,rect)
    x0,y0,x1,y1=rect
    draw.text((x0+17,y1-30), label, font=font(16), fill=(85,74,62))

def rectmap(spec):
    return {p["name"]: tuple(map(int,p["rect_px"])) for p in spec["panels"]}

def xy_on_top(spec, cx, cy, size):
    x0,y0,x1,y1=rectmap(spec)["top"]
    w,d=spec["dimensions_m"]["width"],spec["dimensions_m"]["depth"]
    px=(x1-x0)/w; py=(y1-y0)/d
    halfw,halfd=size[0]/2,size[1]/2
    return (round(x0+(cx-halfw+w/2)*px),round(y1-(cy+halfd+d/2)*py),
            round(x0+(cx+halfw+w/2)*px),round(y1-(cy-halfd+d/2)*py))

def paint(spec):
    w,h=spec["atlas"]["size_px"]
    art=Image.new("RGB",(w,h),(39,39,42))
    parts=Image.new("RGB",(w,h),(16,16,18))
    depth=Image.new("RGB",(w,h),(255,255,255))
    rects=rectmap(spec)
    d=ImageDraw.Draw(art)
    p=ImageDraw.Draw(parts)
    q=ImageDraw.Draw(depth)
    colours={
        "front":(196,182,147), "back":(154,144,124),
        "left":(184,170,140),"right":(182,167,135),
        "top":(142,143,139),"bottom":(77,75,70),
    }
    ids={"front":(172,190,70),"back":(100,136,70),"left":(187,128,45),
         "right":(196,156,50),"top":(64,157,221),"bottom":(95,79,124)}
    chip_palette=[(115,101,82),(239,221,174),(165,125,78),
                  (75,72,63),(207,196,166)]
    for name,r in rects.items():
        textured_region(art,r,colours[name],7 if name!="top" else 13)
        x0,y0,x1,y1=r
        p.rectangle((x0,y0,x1-1,y1-1),fill=ids[name])
        edging(d,r)
        painterly_chips(d,r,65 if name!="bottom" else 15,chip_palette)
        # Angular brushstroke shadows, rather than blurred photographic lighting.
        for k in range(12):
            xx=R.randint(x0+20,x1-35); yy=R.randint(y0+20,y1-23)
            ww=R.randint(8,40)
            d.line([(xx,yy),(xx+ww,yy-4)],fill=R.choice([(116,111,95),(205,199,169),(161,148,123)]),width=R.randint(1,4))
    # Front: painted enamel, brass inset trim, faded manufacturer badge.
    x0,y0,x1,y1=rects["front"]
    d.polygon([(x0+13,y0+22),(x1-13,y0+22),(x1-20,y0+48),(x0+20,y0+48)],fill=(115,103,81))
    d.line([(x0+21,y0+57),(x1-21,y0+57)],fill=(223,199,145),width=7)
    d.line([(x0+24,y1-70),(x1-24,y1-70)],fill=(112,93,76),width=4)
    d.text((x0+146,y0+105),"NORTHERN",font=font(34),fill=(89,79,62),stroke_width=1)
    d.text((x0+194,y0+145),"T-74",font=font(22),fill=(87,76,65))
    # Vintage softly recessed front panel line, with physical 2 mm step.
    d.rounded_rectangle((x0+34,y0+83,x1-34,y1-38),radius=10,outline=(118,105,83),width=5)
    d.line([(x0+36,y0+90),(x1-38,y0+90)],fill=(241,222,177),width=4)
    q.rounded_rectangle((x0+44,y0+92,x1-44,y1-47),radius=12,outline=(255,229,229),width=7)
    # Back: heat vents and cable anchor.
    x0,y0,x1,y1=rects["back"]
    d.rectangle((x0+28,y0+26,x1-28,y0+194),fill=(104,102,95),outline=(210,202,178),width=6)
    for j in range(7):
        yy=y0+52+j*18
        d.rounded_rectangle((x0+58,yy,x1-58,yy+9),radius=4,fill=(48,52,50))
    d.ellipse((x0+230,y1-71,x0+284,y1-17),fill=(46,46,43),outline=(176,170,156),width=5)
    # Left side: conventional plain enamel, chipped along seams.
    x0,y0,x1,y1=rects["left"]
    d.line([(x0+30,y0+68),(x1-30,y0+68)],fill=(117,103,79),width=5)
    d.polygon([(x0+41,y1-46),(x1-70,y1-39),(x1-95,y1-30),(x0+50,y1-32)],fill=(128,109,80))
    # Right side: control-track graphic and mechanical dial backing.
    x0,y0,x1,y1=rects["right"]
    lever_x=x0+round((-.050+.11)/.22*(x1-x0))
    knob_x=x0+round((.053+.11)/.22*(x1-x0))
    lever_y=y1-round((.125-.018)/.18*(y1-y0))
    knob_y=y1-round((.089-.018)/.18*(y1-y0))
    d.rounded_rectangle((lever_x-21,lever_y-83,lever_x+21,lever_y+76),
                        radius=13,fill=(73,75,72),outline=(223,199,147),width=6)
    d.rounded_rectangle((lever_x-5,lever_y-56,lever_x+5,lever_y+47),radius=4,fill=(34,34,36))
    d.ellipse((knob_x-49,knob_y-49,knob_x+49,knob_y+49),
              fill=(88,85,75),outline=(233,212,163),width=7)
    d.ellipse((knob_x-28,knob_y-28,knob_x+28,knob_y+28),
              fill=(50,48,44),outline=(195,184,156),width=5)
    d.line([(knob_x,knob_y),(knob_x+15,knob_y-18)],fill=(239,232,209),width=5)
    d.text((knob_x+55,knob_y-8),"1 2 3 4",font=font(14),fill=(59,55,49))
    q.ellipse((knob_x-51,knob_y-51,knob_x+51,knob_y+51),fill=(212,212,255))
    q.rounded_rectangle((lever_x-22,lever_y-84,lever_x+22,lever_y+77),radius=12,fill=(255,210,210))
    p.ellipse((knob_x-52,knob_y-52,knob_x+52,knob_y+52),fill=(237,107,41))
    p.rounded_rectangle((lever_x-22,lever_y-84,lever_x+22,lever_y+77),radius=12,fill=(209,77,63))
    # Top: brushed steel with two aligned physical cavities (not painted fake slots).
    x0,y0,x1,y1=rects["top"]
    d.rounded_rectangle((x0+17,y0+17,x1-17,y1-17),radius=40,
                        fill=(150,151,146),outline=(64,67,67),width=8)
    for i in range(34):
        yy=y0+25+i*9
        d.line([(x0+32,yy),(x1-32,yy-4)],fill=R.choice([(173,172,164),(135,136,133),
                  (202,199,186),(114,118,117)]),width=R.randint(1,3))
    d.rounded_rectangle((x0+26,y0+23,x1-26,y1-24),radius=34,
                        outline=(210,206,186),width=9)
    # Rounded central dome through blue in depth map, reaching 9mm at centre.
    dw=x1-x0;dh=y1-y0
    yy,xx=np.mgrid[0:dh,0:dw]
    fu=(xx+.5)/dw; fv=(dh-yy-.5)/dh
    dome= .65 * (np.sin(np.pi*fu)**2) * (np.sin(np.pi*fv)**2)
    dp=np.empty((dh,dw,3),dtype=np.uint8)
    dp[:,:,0]=np.uint8(np.clip(255*(1-dome),0,255))
    dp[:,:,1]=dp[:,:,0]
    dp[:,:,2]=255
    depth.paste(Image.fromarray(dp,"RGB"),(x0,y0))
    for cavity in spec["assembly"]["cavities"]:
        box=xy_on_top(spec,cavity["centre_xy_m"][0],cavity["centre_xy_m"][1],cavity["size_xy_m"])
        xa,ya,xb,yb=box
        d.rounded_rectangle((xa-12,ya-10,xb+12,yb+10),radius=17,fill=(91,92,88),outline=(213,211,191),width=6)
        d.rounded_rectangle(box,radius=8,fill=(31,33,34),outline=(84,88,87),width=3)
        for xx2 in range(xa+7,xb-3,9):
            d.line([(xx2,ya+15),(xx2,yb-15)],fill=(43,46,47),width=3)
        q.rounded_rectangle(box,radius=8,fill=(255,0,0))
        p.rounded_rectangle(box,radius=8,fill=(237,48,53))
    # Bottom dark plate
    x0,y0,x1,y1=rects["bottom"]
    d.rounded_rectangle((x0+29,y0+31,x1-29,y1-31),
                        radius=24,fill=(77,77,72),outline=(129,124,112),width=4)
    d.text((x0+130,y0+140),"120V  60Hz",font=font(22),fill=(177,170,152))
    # Reusable small UV tiles for interior cavity walls/floors and hardware.
    for name,r in spec["component_atlas"].items():
        if name=="slot_wall":
            textured_region(art,r,(58,61,62),6)
            t=ImageDraw.Draw(art)
            for k in range(11):
                x=r[0]+13+k*31
                t.line([(x,r[1]+7),(x+6,r[3]-7)],fill=(111,112,106),width=3)
            p.rectangle(r,fill=(229,75,48))
        elif name=="slot_floor":
            textured_region(art,r,(35,36,38),8)
            p.rectangle(r,fill=(39,51,80))
        elif name=="lever":
            textured_region(art,r,(88,83,75),10)
            t=ImageDraw.Draw(art)
            t.rounded_rectangle((r[0]+15,r[1]+17,r[2]-15,r[3]-17),radius=18,outline=(224,205,174),width=8)
            p.rectangle(r,fill=(217,166,67))
        elif name=="knob":
            textured_region(art,r,(50,51,50),8)
            t=ImageDraw.Draw(art)
            cx=(r[0]+r[2])/2;cy=(r[1]+r[3])/2
            for rad in (56,47,27):
                t.ellipse((cx-rad,cy-rad,cx+rad,cy+rad),outline=(164,153,127),width=4)
            t.line([(cx,cy),(cx+20,cy-30)],fill=(238,217,165),width=5)
            p.rectangle(r,fill=(226,107,37))
        else:
            textured_region(art,r,(54,50,47),4)
            p.rectangle(r,fill=(107,94,90))
    # UV guide is a transparent engineering overlay on top of the painted art.
    guide=art.copy()
    g=ImageDraw.Draw(guide)
    for name,r in {**rects, **spec["component_atlas"]}.items():
        x0,y0,x1,y1=r
        g.rectangle((x0-2,y0-2,x1+2,y1+2),outline=(245,218,91),width=4)
        panel=next((p for p in spec["panels"] if p["name"]==name),None)
        label=name.upper()+("  %dx%d mm"%(round(panel["size_m"][0]*1000),round(panel["size_m"][1]*1000)) if panel else "")
        g.text((x0+8,y0+7),label,font=font(16),fill=(16,16,17),stroke_width=2,stroke_fill=(240,224,160))
    # Multi-view source board in a genuinely orthographic unshaded sheet.
    board=Image.new("RGB",(1428,1020),(34,35,39))
    bd=ImageDraw.Draw(board)
    positions=[("front",(24,65)),("back",(500,65)),("left",(1000,65)),
               ("right",(1000,530)),("top",(24,530)),("bottom",(500,530))]
    for label,(bx,by) in positions:
        crop=art.crop(rects[label])
        w0,h0=crop.size
        scale=min(440/w0,355/h0)
        crop=crop.resize((round(w0*scale),round(h0*scale)),Image.Resampling.LANCZOS)
        board.paste(crop,(bx,by+32))
        bd.text((bx+5,by),label.upper()+" — ORTHOGRAPHIC",font=font(20),fill=(237,220,185))
    bd.text((24,980),"1974 TOASTER  |  320 × 220 × 198 mm  |  UV NET IS DIMENSIONED",font=font(17),fill=(191,182,156))
    legend=Image.new("RGB",(1100,240),(27,28,32))
    lg=ImageDraw.Draw(legend)
    lg.text((24,12),"SIGNED DEPTH MAP — RED IN / WHITE ZERO / BLUE OUT",font=font(22),fill=(240,225,199))
    for i in range(1000):
        v=(i/999)*2-1
        if v<0: color=(255,round(255*(1+v)),round(255*(1+v)))
        else: color=(round(255*(1-v)),round(255*(1-v)),255)
        lg.rectangle((50+i,80,51+i,126),fill=color)
    for i,(x,lbl) in enumerate(((50,"−25 mm  RECESSED"),(548,"0 mm  NEUTRAL"),(946,"+14 mm  RAISED"))):
        lg.text((x,146),lbl,font=font(19),fill=(228,225,215))
    paths={"albedo":"toaster_albedo.png","depth":"toaster_depth_rgb.png","parts":"toaster_parts.png","guide":"toaster_uv_guide.png","orthographic":"toaster_six_views.png","legend":"toaster_depth_legend.png"}
    for key,img in (("albedo",art),("depth",depth),("parts",parts),("guide",guide),("orthographic",board),("legend",legend)):
        img.save(os.path.join(ROOT,paths[key]),optimize=True)
    return paths

if __name__=="__main__":
    with open(MANIFEST,encoding="utf8") as f: spec=json.load(f)
    print("TOASTER_SHEETS",paint(spec))
