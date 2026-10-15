"""The water tower intro, as a game-style 3D replay of the real build: Blender 5.2, headless or from the Blender MCP.

    blender -b --factory-startup -P tank_scene.py -- OUT_DIR --plan PLAN.json [--still SECONDS] [--draft]
                                                             [--save FILE.blend]

Builds its own scene ("TankIntro"; re-running replaces it, nothing else is touched) from intro.py's schedule
(PLAN.json): the site as the camera across the road sees it (the yard, the green container, the porta-potties, the
spoil pile, the orange safety fence, the lamp post and sign, the old water tower in the distance), then the job as it
happened, act by act:

1. the finished tank as a cyan blueprint over the site;
2. the site appears: the climbing form at ground level;
3. the replay: for every pour, a mixer truck backs up to the boom pump, the boom unfolds over the pedestal, concrete
   pours into the form, the lift fills and the climbing form jumps up; the telehandler shuttles panels, the crew
   works on the deck, an excavator works the spoil pile;
4. the rest of the job as a hologram: the pedestal to full height, the bowl built at ground and jacked up, paint;
5. a slow orbit around the finished tank.

Cel shading (Shader to RGB + a constant ramp), inverted-hull outlines, bloom in the compositor. Renders
OUT_DIR\\f_0001.png... (or one still). The HUD (dates, counters, captions, specs) is drawn over the frames by
intro.py.
"""
import json
import math
import random
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

FPS = 30
W, H = 1440, 1080
SCENE = "TankIntro"
FONT = r"C:\Windows\Fonts\bahnschrift.ttf"

# dimensions, metres. The pedestal's lift is 0.115 of its diameter (the lift lines in the footage); the rest is
# illustrative (about right for a 0.5 MG composite tank, not the drawings)
PED_R = 5.5
LIFT = 1.25
FOOT_Z = 0.6                    # top of the ring footing
FORM_T = 0.3                    # the climbing form's panels stand off the wall this much
# the bowl's profile (radius, height above its bottom): cone, knuckle, shell, roof
BOWL = [(PED_R + 0.12, 0.0), (8.5, 3.4), (10.6, 5.8), (11.3, 7.0), (11.4, 7.6), (11.4, 10.4), (11.1, 11.0),
        (9.0, 12.0), (5.0, 13.1), (1.4, 13.6), (0.0, 13.7)]

# the site (metres; the pedestal at the origin, north +y, the camera across the road to the south)
PUMP_AT = (-20.0, -2.0)         # the boom pump truck's centre (it faces north, its boom turret behind the cab)
TURRET = (-20.0, 1.2, 3.4)
BOOM_L = [10.0, 9.0, 8.0, 6.5]
HOSE_L = 3.0
MIXER_PARK = (-20.0, -14.8)     # backed up to the pump's hopper, cab to the south
ROAD_Y = -56.0
VIEW = (24.0, -66.0, 4.5)       # the real camera, across the road
AWAY_Y = -170.0                 # where the trucks come from and go (down the road, behind the camera)


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {"out": a[0] if a and not a[0].startswith("--") else None, "plan": None, "still": None, "save": None,
           "draft": "--draft" in a}
    for i, v in enumerate(a):
        if v == "--plan":
            with open(a[i + 1], encoding="utf-8") as fh:
                out["plan"] = json.load(fh)
        elif v == "--still":
            out["still"] = float(a[i + 1])
        elif v == "--save":
            out["save"] = a[i + 1]
    return out


def f(t: float) -> int:
    return int(round(t * FPS)) + 1


# --- scene --------------------------------------------------------------------------------------------------------

def fresh_scene(end_s: float, draft: bool) -> bpy.types.Scene:
    old = bpy.data.scenes.get(SCENE)
    if old is not None:
        for o in list(old.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.scenes.remove(old)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for blk in list(coll):
            if blk.users == 0:
                coll.remove(blk)
    sc = bpy.data.scenes.new(SCENE)
    if bpy.context.window is not None:
        bpy.context.window.scene = sc
    r = sc.render
    r.engine = "BLENDER_EEVEE"
    r.resolution_x, r.resolution_y = W, H
    r.resolution_percentage = 50 if draft else 100
    r.fps = FPS
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.film_transparent = False
    sc.frame_start, sc.frame_end = 1, f(end_s) - 1
    sc.eevee.taa_render_samples = 8 if draft else 24
    try:
        sc.view_settings.view_transform = "Standard"     # flat, saturated game colours (AgX greys them)
    except TypeError:
        pass
    world = bpy.data.worlds.new(SCENE + "_sky")
    sc.world = world
    sky(world)
    return sc


def sky(world):
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bg.inputs[0])
    nt.links.new(bg.outputs[0], out.inputs[0])
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.55, 0.78, 0.95, 1)    # horizon haze
    els[1].position, els[1].color = 0.35, (0.05, 0.28, 0.75, 1)   # deep game blue
    e = els.new(0.08)
    e.color = (0.30, 0.62, 0.95, 1)
    bg.inputs["Strength"].default_value = 1.0


def bloom(sc):
    """Compositor (5.x: a node group): render -> glare (bloom) -> output. Skipped if the API differs."""
    try:
        ng = bpy.data.node_groups.new(SCENE + "_comp", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        rl.scene = sc
        gl = ng.nodes.new("CompositorNodeGlare")
        gl.inputs["Type"].default_value = "Bloom"
        gl.inputs["Threshold"].default_value = 0.85
        gl.inputs["Strength"].default_value = 0.6
        gl.inputs["Size"].default_value = 0.6
        out = ng.nodes.new("NodeGroupOutput")
        ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
        ng.links.new(gl.outputs[0], out.inputs[0])
        sc.compositing_node_group = ng
    except Exception as e:  # noqa: BLE001 - bloom is a nicety
        print("bloom skipped:", e)


# --- materials ----------------------------------------------------------------------------------------------------

def toon(name, color, shade=0.5, lift=1.12):
    """Cel shading: diffuse -> Shader to RGB -> a constant three-step ramp -> emission (flat, game-like)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    dif = nt.nodes.new("ShaderNodeBsdfDiffuse")
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    em = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    base = nt.nodes.new("ShaderNodeRGB")
    base.name = "base"
    base.outputs[0].default_value = (*color, 1)
    nt.links.new(dif.outputs[0], s2r.inputs[0])
    nt.links.new(s2r.outputs[0], bw.inputs[0])
    nt.links.new(bw.outputs[0], ramp.inputs[0])
    ramp.color_ramp.interpolation = "CONSTANT"
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (shade * 0.85, shade * 0.9, shade * 1.15, 1)   # cool shadow
    els[1].position, els[1].color = 0.12, (1, 1, 1, 1)
    hi = els.new(0.62)
    hi.color = (lift, lift, lift * 0.97, 1)
    nt.links.new(ramp.outputs[0], mix.inputs["A"])
    nt.links.new(base.outputs[0], mix.inputs["B"])
    nt.links.new(mix.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def flat(name, color, strength=1.0):
    """Unshaded emission (the concrete stream, lamps)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def outline_mat():
    m = flat("outline", (0.02, 0.03, 0.06))
    m.use_backface_culling = True
    return m


def holo_mat(name):
    """The blueprint: cyan glow lines, fading in and out (its 'fade' value node is keyed)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.2, 0.95, 1.0, 1)
    em.inputs["Strength"].default_value = 3.0
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    fade = nt.nodes.new("ShaderNodeValue")
    fade.name = "fade"
    fade.outputs[0].default_value = 0.0
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(fade.outputs[0], mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    try:
        m.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    return m


def paintable(name, raw, painted, glow=None):
    """A toon material whose base colour goes from raw to painted (the 'paint' value node is keyed)."""
    m = toon(name, raw)
    nt = m.node_tree
    base = nt.nodes["base"]
    p = nt.nodes.new("ShaderNodeRGB")
    p.outputs[0].default_value = (*painted, 1)
    val = nt.nodes.new("ShaderNodeValue")
    val.name = "paint"
    val.outputs[0].default_value = 0.0
    mx = nt.nodes.new("ShaderNodeMix")
    mx.data_type = "RGBA"
    nt.links.new(val.outputs[0], mx.inputs["Factor"])
    nt.links.new(base.outputs[0], mx.inputs["A"])
    nt.links.new(p.outputs[0], mx.inputs["B"])
    target = [l for l in nt.links if l.from_node == base][0].to_socket
    nt.links.new(mx.outputs["Result"], target)
    if glow:
        em = [n for n in nt.nodes if n.type == "EMISSION"][0]
        g = nt.nodes.new("ShaderNodeValue")
        g.name = "glow"
        g.outputs[0].default_value = 1.0
        nt.links.new(g.outputs[0], em.inputs["Strength"])
    return m


# --- geometry -----------------------------------------------------------------------------------------------------

def mesh_obj(name, bm, coll, mats, outline=0.0, outline_m=None, smooth=True):
    """smooth: round surfaces (walls and the bowl) shade smooth; flat caps and low-poly props stay faceted."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth and abs(p.normal.z) < 0.97
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    for m in mats:
        me.materials.append(m)
    if outline > 0 and outline_m is not None:
        me.materials.append(outline_m)
        so = ob.modifiers.new("outline", "SOLIDIFY")
        so.thickness = outline
        so.offset = 1.0
        so.use_flip_normals = True
        so.use_rim = False
        so.material_offset = len(me.materials) - 1
    return ob


def cylinder(r, h, segs=48, z0=0.0, cap=True, r2=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=cap, cap_tris=False, segments=segs, radius1=r,
                          radius2=r if r2 is None else r2, depth=h)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, h / 2 + z0))
    return bm


def box(sx, sy, sz, at=(0, 0, 0)):
    """sx x sy x sz, centred on at in x and y, standing on at's z."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    bmesh.ops.translate(bm, verts=bm.verts, vec=Vector(at) + Vector((0, 0, sz / 2)))
    return bm


def lathe(profile, segs=64):
    """A surface of revolution about Z from (radius, z) points."""
    bm = bmesh.new()
    rings = []
    for (r, z) in profile:
        ring = []
        for i in range(segs):
            a = 2 * math.pi * i / segs
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)) if r > 1e-6 else None)
        rings.append(ring)
    for k in range(len(rings) - 1):
        a_, b_ = rings[k], rings[k + 1]
        for i in range(segs):
            j = (i + 1) % segs
            if b_[0] is None:
                bm.faces.new((a_[i], a_[j], bm.verts.new(b_[i].co if b_[i] else (0, 0, profile[k + 1][1]))))
            else:
                bm.faces.new((a_[i], a_[j], b_[j], b_[i]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def ring_mesh(r, thick, z, segs=64):
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=False, segments=segs, radius=r)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, z))
    ext = bmesh.ops.extrude_edge_only(bm, edges=bm.edges[:])
    bmesh.ops.translate(bm, verts=[v for v in ext["geom"] if isinstance(v, bmesh.types.BMVert)], vec=(0, 0, thick))
    return bm


def tube(r_in, r_out, h, segs=48, z0=0.0):
    """A thick-walled ring (the climbing form's panels, a deck)."""
    bm = bmesh.new()
    outer = [bm.verts.new((r_out * math.cos(2 * math.pi * i / segs), r_out * math.sin(2 * math.pi * i / segs), z0))
             for i in range(segs)]
    inner = [bm.verts.new((r_in * math.cos(2 * math.pi * i / segs), r_in * math.sin(2 * math.pi * i / segs), z0))
             for i in range(segs)]
    for i in range(segs):
        j = (i + 1) % segs
        bm.faces.new((outer[i], outer[j], inner[j], inner[i]))
    ext = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    bmesh.ops.translate(bm, verts=[v for v in ext["geom"] if isinstance(v, bmesh.types.BMVert)], vec=(0, 0, h))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def mound(r, h, seed, segs=18, rings=5):
    """A lumpy pile (the spoil pile)."""
    rnd = random.Random(seed)
    bm = bmesh.new()
    grid = []
    for k in range(rings + 1):
        rr = r * (1 - k / rings)
        zz = h * math.sin(math.pi / 2 * k / rings)
        row = []
        for i in range(segs):
            a = 2 * math.pi * i / segs
            j = rnd.uniform(0.82, 1.15) if 0 < k < rings else 1.0
            row.append(bm.verts.new((rr * j * math.cos(a) * 1.25, rr * j * math.sin(a), zz * (rnd.uniform(0.9, 1.1)
                                                                                             if k else 0))))
        grid.append(row)
    for k in range(rings):
        for i in range(segs):
            j = (i + 1) % segs
            bm.faces.new((grid[k][i], grid[k][j], grid[k + 1][j], grid[k + 1][i]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-3)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def empty(name, coll, parent=None, loc=(0, 0, 0), rot=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    coll.objects.link(e)
    e.parent = parent
    e.location, e.rotation_euler = loc, rot
    return e


def part(name, bm, coll, mats, parent, loc=(0, 0, 0), rot=(0, 0, 0), outline=0.04, ol=None, smooth=False):
    ob = mesh_obj(name, bm, coll, mats, outline, ol, smooth)
    ob.parent = parent
    ob.location, ob.rotation_euler = loc, rot
    return ob


def wrapped_text(text, radius, z_mid, height, coll, mat, depth=0.08, turn=0.0):
    """Text wrapped onto a cylinder of the given radius, centred facing -Y."""
    cu = bpy.data.curves.new("logo_txt", "FONT")
    cu.body = text
    try:
        cu.font = bpy.data.fonts.load(FONT, check_existing=True)
    except RuntimeError:
        pass
    cu.align_x, cu.align_y = "CENTER", "CENTER"
    cu.size = height
    cu.extrude = depth
    tmp = bpy.data.objects.new("logo_tmp", cu)
    coll.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    bpy.data.objects.remove(tmp, do_unlink=True)
    for v in me.vertices:
        x, y, z = v.co
        a = x / radius + turn
        rr = radius + depth + z
        v.co = (rr * math.sin(a), -rr * math.cos(a), z_mid + y)
    me.materials.append(mat)
    ob = bpy.data.objects.new("logo", me)
    coll.objects.link(ob)
    return ob


# --- animation helpers --------------------------------------------------------------------------------------------

def all_fcurves(action):
    """5.x layered actions: the f-curves live in channelbags under layers/strips."""
    if action is None:
        return []
    if hasattr(action, "fcurves") and len(getattr(action, "fcurves", [])):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def key(obj, path, t_or_frame, value, interp="BEZIER", easing="AUTO", frame=False):
    fr = t_or_frame if frame else f(t_or_frame)
    setattr(obj, path, value) if path in ("location", "scale", "rotation_euler") else exec(f"obj.{path} = value")
    obj.keyframe_insert(data_path=path, frame=fr)
    for fc in all_fcurves(obj.animation_data.action):
        if fc.data_path == path:
            for kp in fc.keyframe_points:
                if int(round(kp.co[0])) == fr:
                    kp.interpolation = interp
                    kp.easing = easing


def show(ob, t0, t1=None):
    """Rendered only from t0 (to t1)."""
    ob.hide_render = True
    ob.keyframe_insert("hide_render", frame=1)
    if f(t0) > 1:
        ob.hide_render = False
        ob.keyframe_insert("hide_render", frame=f(t0))
    else:
        ob.hide_render = False
        ob.keyframe_insert("hide_render", frame=1)
    if t1 is not None:
        ob.hide_render = True
        ob.keyframe_insert("hide_render", frame=max(f(t0) + 1, f(t1)))


def pop_in(ob, t, dur=0.35, start=(1, 1, 0), overshoot=True, until=None):
    """Hidden until t, then grows from start (scale per axis; default: up from its base) with a little bounce."""
    s0 = tuple(max(1e-3, v) for v in start)
    show(ob, t, until)
    if f(t) > 1:
        key(ob, "scale", 1, s0, frame=True)
    key(ob, "scale", max(1, f(t)), s0, "BACK" if overshoot else "BEZIER", "EASE_OUT", frame=True)
    key(ob, "scale", t + dur, (1, 1, 1))


def pop_out(ob, t, dur=0.35):
    key(ob, "scale", t, (1, 1, 1))
    key(ob, "scale", t + dur, (1e-3, 1e-3, 1e-3))
    ob.hide_render = True
    ob.keyframe_insert("hide_render", frame=f(t + dur))


def node_key(mat, node, t, value):
    sock = mat.node_tree.nodes[node].outputs[0]
    sock.default_value = value
    sock.keyframe_insert("default_value", frame=f(t))


def wobble(ob, strength=0.25, scale=12.0, seed=0):
    """A little life (a worker shifting about): noise on its location and heading."""
    key(ob, "location", 1, tuple(ob.location), frame=True)
    key(ob, "rotation_euler", 1, tuple(ob.rotation_euler), frame=True)
    for fc in all_fcurves(ob.animation_data.action):
        if (fc.data_path == "location" and fc.array_index < 2) or (fc.data_path == "rotation_euler"
                                                                    and fc.array_index == 2):
            m = fc.modifiers.new("NOISE")
            m.scale = scale
            m.strength = strength if fc.data_path == "location" else strength * 3
            m.phase = seed * 7.3 + fc.array_index * 3.1


# --- the site -----------------------------------------------------------------------------------------------------

class Kit:
    """Materials shared by everything."""

    def __init__(self):
        self.ol = outline_mat()
        self.grass = toon("grass", (0.25, 0.62, 0.22))
        self.field = toon("field", (0.42, 0.64, 0.26))
        self.dirt = toon("dirt", (0.40, 0.31, 0.23))
        self.gravel = toon("gravel", (0.58, 0.55, 0.50))
        self.spoil = toon("spoil", (0.17, 0.14, 0.12))
        self.concrete = toon("concrete", (0.64, 0.63, 0.60))
        self.seam = toon("seam", (0.45, 0.44, 0.42))
        self.footing = toon("footing", (0.62, 0.62, 0.60))
        self.form = toon("form", (0.20, 0.42, 0.78))
        self.form_dark = toon("form_dark", (0.10, 0.22, 0.45))
        self.steel = toon("steel", (0.30, 0.32, 0.35))
        self.boom = toon("boom", (0.22, 0.23, 0.26))
        self.white = toon("white", (0.92, 0.93, 0.95))
        self.red = toon("red", (0.80, 0.12, 0.10))
        self.orange = toon("orange", (0.98, 0.40, 0.06))
        self.yellow = toon("yellow", (0.98, 0.76, 0.08))
        self.green = toon("container", (0.13, 0.42, 0.25))
        self.blue = toon("tarp", (0.10, 0.35, 0.85))
        self.black = toon("tyre", (0.07, 0.07, 0.08))
        self.glass = toon("glass", (0.25, 0.40, 0.52))
        self.hivis = toon("hivis", (0.80, 1.00, 0.10))
        self.skin = toon("skin", (0.80, 0.60, 0.45))
        self.door = toon("door", (0.55, 0.32, 0.14))
        self.dark = toon("dark", (0.12, 0.11, 0.10))
        self.leaf = toon("leaf", (0.16, 0.48, 0.20))
        self.leaf2 = toon("leaf2", (0.12, 0.38, 0.16))
        self.trunk = toon("trunk", (0.40, 0.26, 0.15))
        self.asphalt = toon("asphalt", (0.22, 0.23, 0.25))
        self.paint_line = toon("line", (0.95, 0.80, 0.15))
        self.fence = toon("fence", (0.70, 0.72, 0.74))
        self.logo = toon("logo", (0.05, 0.25, 0.75))
        self.bowl = paintable("bowl", (0.50, 0.53, 0.57), (0.86, 0.89, 0.93), glow=True)
        self.old_tower = toon("old_tower", (0.90, 0.92, 0.95))
        self.slurry = flat("slurry", (0.62, 0.62, 0.60), 1.2)
        self.lamp = flat("lamp", (1.0, 0.95, 0.8), 2.0)


def tree(x, y, s, coll, k: Kit, rnd):
    t = mesh_obj("trunk", cylinder(0.35 * s, 2.2 * s, 6), coll, [k.trunk], 0.06 * s, k.ol, smooth=False)
    t.location = (x, y, 0)
    bm = bmesh.new()
    if rnd.random() < 0.6:   # the real tree line is broadleaf: round crowns, a few conifers
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=2.6 * s)
        bmesh.ops.scale(bm, vec=(1, 1, 0.85), verts=bm.verts)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 4.2 * s))
    else:
        bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=2.2 * s, radius2=0.0, depth=5.5 * s)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 2.75 * s + 1.6 * s))
    c = mesh_obj("leaves", bm, coll, [k.leaf if rnd.random() < 0.5 else k.leaf2], 0.08 * s, k.ol, smooth=False)
    c.location = (x, y, 0)


def wheels(root, coll, k, xs, ys, r=0.55):
    for x in xs:
        for y in ys:
            part("wheel", cylinder(r, 0.45, 12), coll, [k.black], root, (x, y, r), (0, math.pi / 2, 0), 0.03, k.ol)


def terrain(coll, k: Kit):
    """Ground, the road with the lamp post and the site sign, the field with its safety fence, the trees, the old
    water tower in the distance and the lattice mast."""
    mesh_obj("ground", cylinder(5000, 0.1, 96, z0=-0.1), coll, [k.grass])
    mesh_obj("field", box(110, 70, 0.04, (100, -5, 0)), coll, [k.field])
    road = mesh_obj("road", box(900, 11, 0.06, (0, ROAD_Y, 0)), coll, [k.asphalt])
    road.name = "road"
    for x in range(-200, 200, 9):
        mesh_obj("line", box(4.5, 0.25, 0.02, (x, ROAD_Y, 0.06)), coll, [k.paint_line])
    mesh_obj("curb", box(900, 1.2, 0.18, (0, ROAD_Y + 6.1, 0)), coll, [k.gravel])
    # the lamp post and the sign by the road (the middle of the real view)
    lp = empty("lamp_post", coll, loc=(24, -50.5, 0))
    part("pole", cylinder(0.18, 11, 8), coll, [k.fence], lp, outline=0.03, ol=k.ol)
    part("arm", box(2.2, 0.15, 0.15, (0.9, 0, 10.8)), coll, [k.fence], lp, outline=0.02, ol=k.ol)
    part("lamp", box(0.8, 0.4, 0.25, (1.9, 0, 10.6)), coll, [k.dark], lp, outline=0.02, ol=k.ol)
    sg = empty("sign", coll, loc=(36, -44, 0))
    for x in (-1.6, 1.6):
        part("post", box(0.15, 0.15, 3.2, (x, 0, 0)), coll, [k.trunk], sg, outline=0.02, ol=k.ol)
    part("board", box(4.2, 0.12, 2.0, (0, -0.1, 1.4)), coll, [k.white], sg, outline=0.03, ol=k.ol)
    # the orange safety fence in the field
    for y in (-6.0, 4.0):
        mesh_obj("silt", box(70, 0.12, 1.0, (88, y, 0)), coll, [k.orange], 0.03, k.ol)
    # trees: the tree line behind the site, woods around; not on the yard, the road or the field
    rnd = random.Random(4)
    n = 0
    while n < 260:
        x, y = rnd.uniform(-260, 260), rnd.uniform(-160, 340)
        if -45 < x < 52 and -52 < y < 22:
            continue
        if abs(y - ROAD_Y) < 14 or (44 < x < 150 and -45 < y < 20) or (x - VIEW[0]) ** 2 + (y - VIEW[1]) ** 2 < 900:
            continue
        if y < ROAD_Y and rnd.random() < 0.85:
            continue
        tree(x, y, rnd.uniform(1.2, 2.3) * (1.25 if 22 < y < 70 else 1.0), coll, k, rnd)
        n += 1
    # the old water tower in the distance (left of the lamp post in the real view) and the lattice mast
    ot = empty("old_tower", coll, loc=(-60, 320, 0))
    part("stem", cylinder(1.6, 30, 16), coll, [k.old_tower], ot, outline=0.15, ol=k.ol, smooth=True)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=7.5)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 34))
    part("ball", bm, coll, [k.old_tower], ot, outline=0.2, ol=k.ol, smooth=True)
    ms = empty("mast", coll, loc=(-28, 75, 0))
    for i in range(3):
        a = 2 * math.pi * i / 3
        leg = part("leg", cylinder(0.12, 46, 5), coll, [k.fence], ms, (0.7 * math.cos(a), 0.7 * math.sin(a), 0))
        leg.rotation_euler = (0, 0, 0)
    for z in range(4, 46, 6):
        part("brace", ring_mesh(0.75, 0.15, z, 6), coll, [k.fence], ms)
    part("light", box(0.4, 0.4, 0.4, (0, 0, 46)), coll, [k.red], ms)


def yard(coll, k: Kit, t_in):
    """The yard as the camera sees it; pops in when the replay starts. Returns what goes when the job's done."""
    objs = []

    def add(ob, dt=0.0):
        pop_in(ob, t_in + dt, 0.3, (1, 1, 0.01) if ob.name.startswith(("yard", "pad")) else (0.01, 0.01, 0.01))
        objs.append(ob)
        return ob

    add(mesh_obj("yard", box(84, 64, 0.05, (6, -18, 0)), coll, [k.dirt]))
    add(mesh_obj("pad", cylinder(13, 0.08, 40, z0=0.02), coll, [k.gravel]))
    sp = mesh_obj("spoil", mound(9.0, 3.6, 7), coll, [k.spoil], 0.1, k.ol)
    sp.location = (33, -27, 0.05)
    add(sp, 0.1)
    # porta-potties, a generator, the tarped material and the blue canopy
    for i, x in enumerate((-9.5, -8.0)):
        pp = mesh_obj("potty", box(1.3, 1.3, 2.3), coll, [k.white], 0.04, k.ol, smooth=False)
        pp.location = (x, -12.5, 0)
        add(pp, 0.15 + 0.05 * i)
    for (x, y, sx, sy, sz, m) in ((6, -15, 2.2, 1.2, 1.4, k.white), (4.5, -18, 3.0, 2.2, 1.6, k.white),
                                  (-3, -15, 2.0, 1.6, 1.0, k.white)):
        b = mesh_obj("gear", box(sx, sy, sz), coll, [m], 0.04, k.ol, smooth=False)
        b.location = (x, y, 0)
        add(b, 0.2)
    cn = empty("canopy", coll, loc=(10, -9, 0))
    for (x, y) in ((-1.4, -1.4), (1.4, -1.4), (-1.4, 1.4), (1.4, 1.4)):
        part("leg", box(0.08, 0.08, 2.4, (x, y, 0)), coll, [k.fence], cn)
    part("roof", cylinder(2.3, 0.7, 4, z0=2.4, r2=0.2), coll, [k.blue], cn, rot=(0, 0, math.pi / 4), outline=0.03,
         ol=k.ol)
    add(cn, 0.25)
    # the green container on a flatbed trailer, behind and right of the pedestal; a second flatbed
    ct = empty("container", coll, loc=(27, 9, 0), rot=(0, 0, math.radians(5)))
    part("bed", box(13, 2.5, 0.35, (0, 0, 1.0)), coll, [k.steel], ct, outline=0.04, ol=k.ol)
    wheels(ct, coll, k, (-4.6, -3.4), (-1.2, 1.2), 0.5)
    part("box", box(6.1, 2.44, 2.6, (2.5, 0, 1.35)), coll, [k.green], ct, outline=0.06, ol=k.ol)
    part("doors", box(0.05, 2.2, 2.3, (5.58, 0, 1.5)), coll, [k.white], ct)
    add(ct, 0.3)
    fb = empty("flatbed", coll, loc=(17, 13, 0))
    part("bed", box(10, 2.5, 0.3, (0, 0, 0.9)), coll, [k.steel], fb, outline=0.04, ol=k.ol)
    for i in range(4):
        part("rebar", box(8, 0.5, 0.25, (0, -0.8 + i * 0.5, 1.2)), coll, [k.orange if i % 2 else k.trunk], fb)
    add(fb, 0.35)
    # form panels waiting on the ground, left of the pedestal (the real yard has a stack of them)
    for i in range(4):
        pn = mesh_obj("panels", box(4.2, 1.3, 0.22), coll, [k.form], 0.03, k.ol, smooth=False)
        pn.location = (-12 + 0.15 * i, 6 + 0.1 * (i % 2), 0.05 + i * 0.24)
        pn.rotation_euler = (0, 0, math.radians(20 + 3 * i))
        add(pn, 0.4)
    # crew pickups and cars in front
    for (x, y, rot, m) in ((-13, -24, 0.1, k.white), (-3, -27, -0.05, k.steel), (6, -25, 0.2, k.dark),
                           (-20, -30, 0.0, k.white)):
        add(pickup(coll, k, (x, y), rot, m), 0.45)
    return objs


def pickup(coll, k, at, rot, paint_m):
    r = empty("pickup", coll, loc=(*at, 0), rot=(0, 0, rot))
    part("bed", box(5.6, 2.0, 0.9, (0, 0, 0.5)), coll, [paint_m], r, outline=0.04, ol=k.ol)
    part("cab", box(2.4, 1.9, 0.9, (0.4, 0, 1.4)), coll, [paint_m], r, outline=0.04, ol=k.ol)
    part("glass", box(2.2, 1.95, 0.55, (0.4, 0, 1.55)), coll, [k.glass], r)
    wheels(r, coll, k, (-1.8, 1.8), (-1.0, 1.0), 0.42)
    return r


# --- the pedestal, its forms and the crew -------------------------------------------------------------------------

def pedestal(coll, k: Kit, n_total: int):
    """Every lift, hidden; the replay shows them as they're poured, the projected act as a hologram."""
    foot = mesh_obj("footing", cylinder(PED_R + 1.4, FOOT_Z, 48), coll, [k.footing], 0.15, k.ol)
    lifts = []
    for i in range(n_total):
        ob = mesh_obj(f"lift_{i + 1:02d}", cylinder(PED_R, LIFT, 48, cap=True), coll, [k.concrete], 0.18, k.ol,
                      smooth=True)
        ob.location = (0, 0, FOOT_Z + i * LIFT)
        sm = mesh_obj(f"seam_{i + 1:02d}", cylinder(PED_R + 0.03, 0.09, 48, z0=LIFT - 0.05, cap=False), coll,
                      [k.seam])
        sm.parent = ob
        lifts.append(ob)
    # the doorways (blockouts in the first lifts): a big arched two-leaf door, a small one to its right
    doors = []
    for (ang, wd, ht) in ((-100, 2.8, 3.6), (-62, 1.2, 2.5)):
        a = math.radians(ang)
        dr = empty("doorway", coll, loc=(PED_R * math.cos(a), PED_R * math.sin(a), FOOT_Z),
                   rot=(0, 0, a + math.pi / 2))
        part("leaf", box(wd, 0.3, ht - wd / 2), coll, [k.dark], dr, outline=0.03, ol=k.ol)
        part("arch", cylinder(wd / 2, 0.3, 16, z0=-0.15), coll, [k.dark], dr, (0, 0, ht - wd / 2),
             (math.pi / 2, 0, 0))
        for x in (-wd / 2, 0, wd / 2):
            part("frame", box(0.12, 0.36, ht - wd / 2), coll, [k.door], dr, (x, 0, 0))
        for z in (ht * 0.3, ht * 0.6):
            part("frame", box(wd, 0.36, 0.1), coll, [k.door], dr, (0, 0, z))
        doors.append(dr)
    return foot, lifts, doors


def climbing_form(coll, k: Kit):
    """Three tiers of blue panels a lift tall, a deck with a rail on top, three lifting gantries. Its origin is its
    bottom; it stands from two lifts below the concrete's top to one above."""
    root = empty("form", coll)
    for i in range(3):
        part("tier", tube(PED_R + 0.02, PED_R + FORM_T, LIFT * 0.96, 48, i * LIFT), coll,
             [k.form if i else k.form_dark], root, outline=0.05, ol=k.ol, smooth=True)
        part("rib", ring_mesh(PED_R + FORM_T + 0.02, 0.12, i * LIFT + LIFT * 0.92), coll, [k.form_dark], root)
    top = 3 * LIFT
    part("deck", tube(PED_R - 0.4, PED_R + 1.4, 0.12, 48, top), coll, [k.steel], root, outline=0.03, ol=k.ol)
    part("rail", ring_mesh(PED_R + 1.35, 0.06, top + 1.05), coll, [k.yellow], root)
    part("rail_mid", ring_mesh(PED_R + 1.35, 0.05, top + 0.55), coll, [k.yellow], root)
    for i in range(24):
        a = 2 * math.pi * i / 24
        part("post", box(0.06, 0.06, 1.1, ((PED_R + 1.35) * math.cos(a), (PED_R + 1.35) * math.sin(a), top)), coll,
             [k.yellow], root)
    # the hanging platform under the lowest tier (the dark band below the blue on the real one)
    part("hang", tube(PED_R + 0.05, PED_R + 1.0, 0.1, 48, -0.6), coll, [k.steel], root, outline=0.02, ol=k.ol)
    for i in range(3):   # the gantries: an A-frame with a hoist
        a = math.radians(70 + 120 * i)
        g = empty("gantry", coll, root, ((PED_R - 0.6) * math.cos(a), (PED_R - 0.6) * math.sin(a), top + 0.12),
                  (0, 0, a))
        for s in (-0.6, 0.6):
            part("leg", box(0.14, 0.14, 2.6), coll, [k.steel], g, (0, s, 0), (s * 0.35, 0, 0), 0.02, k.ol)
        part("beam", box(1.8, 0.16, 0.16, (0.4, 0, 2.45)), coll, [k.steel], g, outline=0.02, ol=k.ol)
        part("hoist", box(0.5, 0.5, 0.5, (1.0, 0, 1.8)), coll, [k.red], g, outline=0.02, ol=k.ol)
    return root


def worker(coll, k: Kit, parent, at, heading=0.0, seed=0):
    r = empty("worker", coll, parent, at, (0, 0, heading))
    part("legs", box(0.36, 0.28, 0.85), coll, [k.dark], r, outline=0.02, ol=k.ol)
    part("body", box(0.5, 0.34, 0.7, (0, 0, 0.85)), coll, [k.hivis], r, outline=0.02, ol=k.ol)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.17)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 1.72))
    part("head", bm, coll, [k.skin], r, outline=0.02, ol=k.ol)
    part("hat", cylinder(0.2, 0.12, 10, z0=1.84), coll, [k.white], r, outline=0.02, ol=k.ol)
    wobble(r, 0.25, 10.0, seed)
    return r


# --- the equipment ------------------------------------------------------------------------------------------------

class Pump:
    """The boom pump truck: a chain of four pivots (pitch about each section's Y), a hose that always hangs
    straight down from the tip, and the concrete stream under it."""

    def __init__(self, coll, k: Kit):
        self.root = root = empty("pump", coll, loc=(*PUMP_AT, 0), rot=(0, 0, math.pi / 2))   # +X = north
        part("chassis", box(12, 2.5, 1.1, (0, 0, 0.8)), coll, [k.steel], root, outline=0.05, ol=k.ol)
        part("cab", box(2.4, 2.5, 2.3, (5.0, 0, 0.9)), coll, [k.white], root, outline=0.05, ol=k.ol)
        part("glass", box(0.1, 2.3, 0.9, (6.2, 0, 2.1)), coll, [k.glass], root)
        part("stripe", box(2.45, 2.55, 0.3, (5.0, 0, 1.3)), coll, [k.red], root)
        part("body", box(8.0, 2.4, 1.0, (-1.5, 0, 1.9)), coll, [k.white], root, outline=0.04, ol=k.ol)
        part("hopper", box(1.4, 2.0, 1.2, (-6.4, 0, 1.2)), coll, [k.steel], root, outline=0.04, ol=k.ol)
        wheels(root, coll, k, (4.6, 0.5, -1.7, -3.9), (-1.2, 1.2), 0.55)
        # outriggers: they swing out and their feet go down (scale x keyed)
        self.legs = []
        for (x, s) in ((3.2, 1), (3.2, -1), (-4.8, 1), (-4.8, -1)):
            pv = empty("outrigger", coll, root, (x, s * 1.1, 1.6), (0, 0, s * math.radians(55 if x > 0 else 125)))
            part("arm", box(4.0, 0.35, 0.35, (2.0, 0, 0)), coll, [k.red], pv, outline=0.03, ol=k.ol)
            part("foot", box(0.25, 0.25, 1.6, (3.9, 0, -1.6)), coll, [k.steel], pv, outline=0.02, ol=k.ol)
            pv.scale = (0.25, 1, 1)
            self.legs.append(pv)
        # the boom: turret (yaw), then four sections, each pitching about its own Y from the last one's tip
        local = (TURRET[1] - PUMP_AT[1], -(TURRET[0] - PUMP_AT[0]), TURRET[2])
        self.turret = empty("turret", coll, root, local)
        part("turret_box", cylinder(0.9, 1.0, 12, z0=-1.0), coll, [k.red], self.turret, outline=0.03, ol=k.ol)
        self.secs = []
        parent = self.turret
        for i, L in enumerate(BOOM_L):
            pv = empty(f"sec{i + 1}", coll, parent, (BOOM_L[i - 1], 0, 0) if i else (0, 0, 0))
            th = 0.7 - 0.1 * i
            part(f"beam{i + 1}", box(L, th, th, (L / 2, 0, -th / 2)), coll, [k.boom], pv, outline=0.05, ol=k.ol)
            part(f"pipe{i + 1}", cylinder(0.09, L, 6), coll, [k.white], pv, (0, th / 2 + 0.05, 0), (0, math.pi / 2, 0))
            self.secs.append(pv)
            parent = pv
        self.tip = empty("tip", coll, self.secs[-1], (BOOM_L[-1], 0, 0))
        self.hose = mesh_obj("hose", cylinder(0.2, HOSE_L, 8, z0=-HOSE_L), coll, [k.black], 0.03, k.ol)
        c = self.hose.constraints.new("COPY_LOCATION")
        c.target = self.tip
        self.stream = mesh_obj("stream", cylinder(0.16, 7.0, 8, z0=-HOSE_L - 7.0), coll, [k.slurry])
        c = self.stream.constraints.new("COPY_LOCATION")
        c.target = self.tip
        self.stream.hide_render = True
        self.stream.keyframe_insert("hide_render", frame=1)
        self.folded = [math.radians(v) for v in (6, 172, -170, 170)]
        self.yaw_rest = 0.0                       # along the truck, over the cab
        self.set_pose(1, self.yaw_rest, self.folded, frame=True)

    def world_turret(self):
        return Vector(TURRET)

    def solve(self, target: Vector):
        """Section angles (relative pitches, radians) and yaw so the tip is right over `target` (the hose hangs
        from the tip): the last section near vertical, the others in the arch the real one makes."""
        tw = self.world_turret()
        dx, dy = target.x - tw.x, target.y - tw.y
        D, Hh = math.hypot(dx, dy), target.z - tw.z
        yaw_world = math.atan2(dy, dx)
        L1, L2, L3, L4 = BOOM_L
        a4 = math.radians(-86)
        best = None
        for step, rng in ((2.0, None), (0.25, 3.0)):
            if rng is None:
                c1, c2, c3 = range(30, 88, 2), range(-40, 50, 2), range(-80, 0, 2)
            else:
                b1, b2, b3 = best[1]
                fr = [i * step for i in range(-int(rng / step), int(rng / step) + 1)]
                c1, c2, c3 = [b1 + v for v in fr], [b2 + v for v in fr], [b3 + v for v in fr]
            for a1 in c1:
                r1 = math.radians(a1)
                x1, z1 = L1 * math.cos(r1), L1 * math.sin(r1)
                for a2 in c2:
                    if a2 > a1:
                        continue
                    r2 = math.radians(a2)
                    x2, z2 = x1 + L2 * math.cos(r2), z1 + L2 * math.sin(r2)
                    for a3 in c3:
                        if a3 > a2:
                            continue
                        r3 = math.radians(a3)
                        x = x2 + L3 * math.cos(r3) + L4 * math.cos(a4)
                        z = z2 + L3 * math.sin(r3) + L4 * math.sin(a4)
                        e = (x - D) ** 2 + (z - Hh) ** 2 + 0.0004 * ((a1 - 68) ** 2 + (a2 - 8) ** 2 + (a3 + 40) ** 2)
                        if best is None or e < best[0]:
                            best = (e, (a1, a2, a3))
        a1, a2, a3 = (math.radians(v) for v in best[1])
        rel = [a1, a2 - a1, a3 - a2, a4 - a3]
        return yaw_world - math.pi / 2, rel        # the truck faces +y: its local X is world +Y

    def set_pose(self, t, yaw, rel, frame=False, interp="BEZIER"):
        key(self.turret, "rotation_euler", t, (0, 0, yaw), interp, frame=frame)
        for pv, r in zip(self.secs, rel):
            key(pv, "rotation_euler", t, (0, -r, 0), interp, frame=frame)

    def unfold(self, t0, t1, yaw, rel):
        """Section by section: the first lifts and slews, then each next one swings out."""
        d = t1 - t0
        key(self.turret, "rotation_euler", t0, (0, 0, self.yaw_rest))
        key(self.turret, "rotation_euler", t0 + 0.55 * d, (0, 0, yaw))
        for i, (pv, a, b) in enumerate(zip(self.secs, self.folded, rel)):
            s0, s1 = t0 + d * 0.15 * i, t0 + d * (0.4 + 0.2 * i)
            key(pv, "rotation_euler", s0, (0, -a, 0))
            key(pv, "rotation_euler", min(t1, s1), (0, -b, 0))

    def fold(self, t0, t1, yaw, rel):
        d = t1 - t0
        for i, (pv, a, b) in enumerate(zip(self.secs, self.folded, rel)):
            j = 3 - i
            s0, s1 = t0 + d * 0.15 * j, t0 + d * (0.4 + 0.2 * j)
            key(pv, "rotation_euler", s0, (0, -b, 0))
            key(pv, "rotation_euler", min(t1, s1), (0, -a, 0))
        key(self.turret, "rotation_euler", t0 + 0.3 * d, (0, 0, yaw))
        key(self.turret, "rotation_euler", t1, (0, 0, self.yaw_rest))

    def pour(self, t0, t1):
        self.stream.hide_render = False
        self.stream.keyframe_insert("hide_render", frame=f(t0))
        self.stream.hide_render = True
        self.stream.keyframe_insert("hide_render", frame=max(f(t0) + 1, f(t1)))

    def outriggers(self, t):
        for pv in self.legs:
            key(pv, "scale", t, (0.25, 1, 1))
            key(pv, "scale", t + 0.4, (1, 1, 1), "BACK", "EASE_OUT")


def mixer(coll, k: Kit, name):
    """A ready-mix truck: white drum with a red band, spinning while it's on site. Faces local +X."""
    root = empty(name, coll, loc=(MIXER_PARK[0], AWAY_Y, 0), rot=(0, 0, -math.pi / 2))   # cab to the south
    part("chassis", box(8.5, 2.4, 0.9, (0, 0, 0.7)), coll, [k.steel], root, outline=0.04, ol=k.ol)
    part("cab", box(2.3, 2.4, 2.4, (3.2, 0, 0.9)), coll, [k.white], root, outline=0.05, ol=k.ol)
    part("glass", box(0.1, 2.2, 0.8, (4.36, 0, 2.2)), coll, [k.glass], root)
    wheels(root, coll, k, (3.0, -1.2, -2.6), (-1.15, 1.15), 0.55)
    drum_pv = empty("drum_pv", coll, root, (-1.4, 0, 2.6), (0, math.radians(-14), 0))
    along_x = Matrix.Rotation(math.pi / 2, 3, "Y")   # the drum turns about its own axis: build it along X
    bm = lathe([(0.05, -2.6), (0.9, -2.4), (1.3, -1.2), (1.35, 0.4), (1.1, 1.6), (0.5, 2.6), (0.0, 2.65)], 18)
    bmesh.ops.rotate(bm, verts=bm.verts, matrix=along_x)
    drum = part("drum", bm, coll, [k.white], drum_pv, outline=0.05, ol=k.ol, smooth=True)
    for nm, (r0, r1, h, z) in (("band", (1.33, 1.42, 0.35, -0.2)), ("band2", (1.2, 1.3, 0.3, 1.2))):
        bm = tube(r0, r1, h, 18, z)
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=along_x)
        part(nm, bm, coll, [k.red], drum)
    part("chute", box(1.6, 0.4, 0.15, (-4.4, 0, 1.7)), coll, [k.orange], root, rot=(0, math.radians(20), 0))
    return root, drum


def telehandler(coll, k: Kit):
    root = empty("telehandler", coll, loc=(12, 3, 0))
    part("body", box(4.6, 2.3, 1.2, (0, 0, 0.8)), coll, [k.orange], root, outline=0.05, ol=k.ol)
    part("cab", box(1.2, 1.0, 1.7, (-0.3, 0.6, 1.9)), coll, [k.dark], root, outline=0.03, ol=k.ol)
    wheels(root, coll, k, (-1.6, 1.6), (-1.15, 1.15), 0.62)
    arm = empty("arm", coll, root, (-1.8, -0.4, 2.0), (0, math.radians(-8), 0))
    part("boom", box(5.5, 0.6, 0.6, (2.6, 0, -0.3)), coll, [k.orange], arm, outline=0.04, ol=k.ol)
    part("forks", box(1.2, 1.2, 0.15, (5.6, 0, -0.9)), coll, [k.steel], arm, outline=0.02, ol=k.ol)
    part("load", box(1.2, 1.1, 0.6, (5.6, 0, -0.75)), coll, [k.form], arm, outline=0.02, ol=k.ol)
    return root, arm


def excavator(coll, k: Kit):
    """The yellow excavator on the spoil pile (10-14). Faces local +X."""
    root = empty("excavator", coll, loc=(46, -22, 0), rot=(0, 0, math.pi))
    for s in (-1.3, 1.3):
        part("track", box(4.6, 0.8, 1.0, (0, s, 0)), coll, [k.dark], root, outline=0.04, ol=k.ol)
    sw = empty("swing", coll, root, (0, 0, 1.0))
    part("house", box(3.6, 2.8, 1.3, (-0.4, 0, 0)), coll, [k.yellow], sw, outline=0.05, ol=k.ol)
    part("cab", box(1.3, 1.1, 1.5, (0.6, 0.8, 1.3)), coll, [k.yellow], sw, outline=0.03, ol=k.ol)
    part("window", box(1.32, 1.12, 0.7, (0.6, 0.8, 1.9)), coll, [k.glass], sw)
    bp = empty("boom", coll, sw, (1.0, -0.3, 1.0), (0, math.radians(-35), 0))
    part("boom_b", box(5.2, 0.55, 0.6, (2.6, 0, 0)), coll, [k.yellow], bp, outline=0.04, ol=k.ol)
    sp = empty("stick", coll, bp, (5.2, 0, 0), (0, math.radians(95), 0))
    part("stick_b", box(3.4, 0.45, 0.45, (1.7, 0, 0)), coll, [k.yellow], sp, outline=0.04, ol=k.ol)
    part("bucket", box(1.0, 1.1, 0.9, (3.6, 0, -0.3)), coll, [k.dark], sp, outline=0.03, ol=k.ol)
    return root, sw, bp, sp


# --- the build ----------------------------------------------------------------------------------------------------

def form_z(lifts: int) -> float:
    """The climbing form's bottom with `lifts` poured: it covers the top two and stands a lift above."""
    return max(0.0, FOOT_Z + (lifts - 2) * LIFT)


def build(plan, draft):
    acts, proj = plan["acts"], plan["projected"]
    end_s = acts["orbit"][1]
    sc = fresh_scene(end_s, draft)
    coll = sc.collection
    k = Kit()
    n_total, lifts0, lifts_now = plan["lifts_total"], plan["lifts0"], plan["lifts_now"]
    top = FOOT_Z + n_total * LIFT
    site0 = acts["site"][0]

    terrain(coll, k)
    junk = yard(coll, k, site0)

    # the pedestal: the lifts there when the camera started, then each as it's poured
    foot, lifts, doors = pedestal(coll, k, n_total)
    pop_in(foot, site0, 0.3, (1, 1, 0.01), overshoot=False)
    for d in doors:
        pop_in(d, site0 + 0.1, 0.3, (0.01, 0.01, 0.01))
    for i in range(lifts0):
        pop_in(lifts[i], site0 + 0.1 + 0.05 * i, 0.3)
    form = climbing_form(coll, k)
    form.location = (0, 0, form_z(lifts0))
    pop_in(form, site0 + 0.2, 0.4, (1, 1, 0.01))
    crew = [worker(coll, k, form, ((PED_R + 0.7) * math.cos(a), (PED_R + 0.7) * math.sin(a), 3 * LIFT + 0.12),
                   a + 1.5, i) for i, a in enumerate((math.radians(-120), math.radians(-80), math.radians(-30),
                                                     math.radians(40)))]
    crew += [worker(coll, k, None, (x, y, 0), h, 9 + i) for i, (x, y, h) in enumerate(((-6, -9, 1.0), (-14, -7, 2.0),
                                                                                         (3, -8, -1.0)))]
    for c in crew:
        pop_in(c, site0 + 0.3, 0.3, (0.01, 0.01, 0.01))
        show_all(c, site0 + 0.3, proj["ghost"][0] + 0.4)

    # the pump, the mixers, the telehandler, the excavator
    pump = Pump(coll, k)
    first_t = plan["pours"][0]["t0"] if plan["pours"] else acts["replay"][0]
    key(pump.root, "location", 1, (PUMP_AT[0], AWAY_Y, 0), frame=True)
    key(pump.root, "location", first_t - 1.2, (PUMP_AT[0], AWAY_Y, 0))
    key(pump.root, "location", first_t - 0.2, (*PUMP_AT, 0))
    show(pump.root, first_t - 1.2)
    for ob in pump.root.children_recursive:
        if ob.type == "MESH":
            show(ob, first_t - 1.2, proj["ghost"][0] + 0.3)
    show(pump.hose, first_t - 1.2, proj["ghost"][0] + 0.3)
    pump.outriggers(first_t - 0.2)
    lifts_at, n = lifts0, lifts0
    pose = None
    for bi, bw in enumerate(plan["booms"]):
        day = [p for p in plan["pours"] if bw["out"][0] - 1e-3 <= p["t0"] <= bw["in"][0] + 1e-3]
        poses = []
        toward = Vector((TURRET[0], TURRET[1], 0)).normalized() * (PED_R - 0.15)   # the wall nearest the pump
        for p in day:
            tgt = Vector((toward.x, toward.y, form_z(n) + 3 * LIFT + HOSE_L + 1.2))
            poses.append(pump.solve(tgt))
            n += len(p["lifts"])
        pose = poses[0]
        pump.unfold(bw["out"][0], bw["out"][1], *pose)
        for p, ps in zip(day[1:], poses[1:]):   # the form rose: lift the boom with it
            pump.set_pose(p["fill"][0] - 0.05, *ps)
        last = poses[-1]
        pump.set_pose(day[-1]["t1"] - 0.02, *last)
        nxt = plan["booms"][bi + 1]["out"][0] if bi + 1 < len(plan["booms"]) else None
        if nxt is None or bw["in"][1] < nxt - 0.05:
            pump.fold(bw["in"][0], bw["in"][1], *last)
    for p in plan["pours"]:
        pump.pour(p["fill"][0], p["fill"][1])
    # the lifts fill as the concrete goes in, then the form jumps
    for p in plan["pours"]:
        if not p["lifts"]:
            continue
        a, b = p["fill"]
        step = (b - a) / len(p["lifts"])
        for j, li in enumerate(p["lifts"]):
            ob = lifts[li - 1]
            show(ob, a + j * step)
            key(ob, "scale", a + j * step, (1, 1, 0.02), "LINEAR")
            key(ob, "scale", a + (j + 1) * step, (1, 1, 1))
        lifts_at = max(lifts_at, max(p["lifts"]))
        j0, j1 = p["jump"]
        key(form, "location", j0, tuple(form.location))
        key(form, "location", j1, (0, 0, form_z(lifts_at)), "BACK", "EASE_OUT")
        form.location = (0, 0, form_z(lifts_at))

    mixers = [mixer(coll, k, "mixer_a"), mixer(coll, k, "mixer_b")]
    busy = [-1.0, -1.0]
    for mv in plan["mixers"]:
        i = 0 if busy[0] <= mv["in"][0] else 1
        root, drum = mixers[i]
        busy[i] = mv["out"][1]
        far = (MIXER_PARK[0], AWAY_Y, 0)
        key(root, "location", mv["in"][0] - 0.02, far, "CONSTANT")
        key(root, "location", mv["in"][0], far)
        key(root, "location", mv["in"][1], (*MIXER_PARK, 0))
        key(root, "location", mv["out"][0], (*MIXER_PARK, 0))
        key(root, "location", mv["out"][1], far)
        spun = drum.rotation_euler.x
        key(drum, "rotation_euler", mv["in"][0], (spun, 0, 0), "LINEAR")
        key(drum, "rotation_euler", mv["out"][1], (spun + math.radians(360 * 1.5 * (mv["out"][1] - mv["in"][0])),
                                                   0, 0), "LINEAR")
    for root, _ in mixers:
        for ob in [root, *root.children_recursive]:
            if ob.type == "MESH":
                show(ob, plan["mixers"][0]["in"][0] if plan["mixers"] else end_s, proj["ghost"][0])

    th, arm = telehandler(coll, k)
    show_all(th, site0 + 0.3, proj["solid"][0])
    t, flip = acts["replay"][0], 0
    while t < proj["ghost"][0]:
        a_, b_ = ((12, 3, 0), (-8, 8, 0)) if flip == 0 else ((-8, 8, 0), (12, 3, 0))
        key(th, "location", t, a_)
        key(th, "location", t + 1.6, b_)
        key(arm, "rotation_euler", t + 1.6, (0, math.radians(-8), 0))
        key(arm, "rotation_euler", t + 2.2, (0, math.radians(-30 if flip == 0 else -8), 0))
        t += 2.6
        flip ^= 1

    ex, sw, bp, sp = excavator(coll, k)
    if plan["digs"]:
        d0 = plan["digs"][0]["t0"]
        key(ex, "location", d0, (62, -22, 0))
        key(ex, "location", d0 + 0.8, (46, -22, 0))
        show_all(ex, d0, proj["solid"][0])
        t = d0 + 0.8
        while t < end_s:
            key(sw, "rotation_euler", t, (0, 0, math.radians(-10)))
            key(bp, "rotation_euler", t, (0, math.radians(-15), 0))
            key(sp, "rotation_euler", t + 0.4, (0, math.radians(110), 0))
            key(sw, "rotation_euler", t + 0.8, (0, 0, math.radians(35)))
            key(bp, "rotation_euler", t + 0.8, (0, math.radians(-40), 0))
            key(sp, "rotation_euler", t + 1.2, (0, math.radians(70), 0))
            t += 1.6
    else:
        show_all(ex, end_s + 1)

    # act 1: the finished tank as a blueprint over the empty site, gone as the real one appears
    holo = holo_mat("blueprint")
    hp = cylinder(PED_R, n_total * LIFT, 32, z0=FOOT_Z)
    hb = lathe(BOWL, 32)
    bmesh.ops.translate(hb, verts=hb.verts, vec=(0, 0, top - 0.3))
    for nm, bm_ in (("holo_ped", hp), ("holo_bowl", hb)):
        ho = mesh_obj(nm, bm_, coll, [holo])
        ho.modifiers.new("wire", "WIREFRAME").thickness = 0.08
        show(ho, 0, site0 + 1.1)
    node_key(holo, "fade", 0, 0.0)
    node_key(holo, "fade", 0.8, 0.95)
    node_key(holo, "fade", site0, 0.95)
    node_key(holo, "fade", site0 + 1.0, 0.0)

    # act 4, still to come: the forms and the trucks go, the rest of the lifts as a hologram, the bowl assembled at
    # ground in hologram and jacked up, then real: raw steel, paint, the logo; the remaining lifts turn to concrete
    g0, g1 = proj["ghost"]
    pop_out(form, g0, 0.4)
    ghost = holo_mat("ghost")
    node_key(ghost, "fade", g0, 0.0)
    node_key(ghost, "fade", g0 + 0.3, 0.9)
    node_key(ghost, "fade", proj["solid"][0], 0.9)
    node_key(ghost, "fade", proj["solid"][0] + 0.6, 0.0)
    rest = list(range(lifts_now, n_total))
    for j, i in enumerate(rest):
        gl = mesh_obj(f"ghost_{i + 1:02d}", cylinder(PED_R, LIFT, 32, z0=FOOT_Z + i * LIFT), coll, [ghost])
        gl.modifiers.new("wire", "WIREFRAME").thickness = 0.06
        tt = g0 + 0.2 + (g1 - g0 - 0.3) * j / max(1, len(rest))
        show(gl, tt, proj["solid"][0] + 0.6)
        ob = lifts[i]
        show(ob, proj["solid"][0] + 0.02 * j)
        key(ob, "scale", proj["solid"][0] + 0.02 * j, (1.04, 1.04, 0.02), "BACK", "EASE_OUT")
        key(ob, "scale", proj["solid"][0] + 0.02 * j + 0.35, (1, 1, 1))
    hbowl = mesh_obj("ghost_bowl", lathe(BOWL, 32), coll, [ghost])
    hbowl.modifiers.new("wire", "WIREFRAME").thickness = 0.08
    hbowl.location = (0, 0, FOOT_Z)
    pop_in(hbowl, proj["bowl"][0], 0.8, (1, 1, 0.01), overshoot=False, until=proj["jack"][1] + 0.05)
    key(hbowl, "location", proj["jack"][0], (0, 0, FOOT_Z))
    key(hbowl, "location", proj["jack"][1], (0, 0, top - 0.3))

    bowl = mesh_obj("bowl", lathe(BOWL), coll, [k.bowl], 0.3, k.ol, smooth=True)
    bowl.location = (0, 0, top - 0.3)
    rail = mesh_obj("rail", ring_mesh(11.15, 0.12, 11.0), coll, [k.fence])
    finial = mesh_obj("finial", cylinder(0.8, 1.6, 12, z0=13.6), coll, [k.bowl], 0.05, k.ol)
    mast = mesh_obj("mast", cylinder(0.12, 5.0, 6, z0=12.2), coll, [k.fence], 0.03, k.ol)
    mast.location = (4.0, 2.0, 0)
    logos = [wrapped_text("BEAR CREEK", 11.4, 8.9, 3.1, coll, k.logo, turn=a_)
             for a_ in (math.radians(30), math.radians(210))]
    for o in (rail, finial, mast, *logos):
        o.parent = bowl
    show(bowl, proj["jack"][1])
    for o in (rail, finial, mast):
        show(o, proj["jack"][1])
    for lg in logos:
        pop_in(lg, (proj["paint"][0] + proj["paint"][1]) / 2, 0.4, (0.01, 0.01, 0.01))
    node_key(k.bowl, "paint", proj["paint"][0], 0.0)
    node_key(k.bowl, "paint", proj["paint"][1], 1.0)
    node_key(k.bowl, "glow", proj["jack"][1], 1.8)
    node_key(k.bowl, "glow", proj["jack"][1] + 0.5, 1.0)
    node_key(k.bowl, "glow", proj["solid"][0], 1.0)
    node_key(k.bowl, "glow", proj["solid"][0] + 0.3, 1.5)
    node_key(k.bowl, "glow", proj["solid"][1], 1.0)
    # the job done: the yard cleared, a fence and a paved apron
    s0 = proj["solid"][0]
    for ob in junk:
        if ob.name.startswith(("yard", "pad", "spoil")):
            continue
        pop_out(ob, s0 + 0.3, 0.3)
    apron = mesh_obj("apron", cylinder(13, 0.06, 48, z0=0.12), coll, [k.asphalt])
    pop_in(apron, s0 + 0.4, 0.4, (1, 1, 0.01), overshoot=False)
    for i in range(36):
        aa = 2 * math.pi * i / 36
        post = mesh_obj("post", cylinder(0.08, 2.0, 6), coll, [k.fence], 0.02, k.ol)
        post.location = (19 * math.cos(aa), 19 * math.sin(aa), 0)
        pop_in(post, s0 + 0.4 + i * 0.01, 0.2)
    fence = mesh_obj("fence", ring_mesh(19, 0.05, 1.9), coll, [k.fence])
    pop_in(fence, s0 + 0.85, 0.25, (0.01, 0.01, 0.01))

    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 3.0
    sun.angle = math.radians(2)
    so = bpy.data.objects.new("sun", sun)
    so.rotation_euler = (math.radians(55), 0, math.radians(-20))   # from the side: a lit and a shadow side
    coll.objects.link(so)

    camera(sc, plan, top, lifts_now)
    bloom(sc)
    return sc


def show_all(root, t0, t1=None):
    for ob in [root, *root.children_recursive]:
        if ob.type == "MESH":
            show(ob, t0, t1)


def camera(sc, plan, top, lifts_now):
    """A swoop in on the blueprint; down to the real camera's view across the road for the replay, pushing in for
    the first pour and craning up with the pedestal; back out for what's still to come; a slow orbit to the end."""
    acts, proj = plan["acts"], plan["projected"]
    cam = bpy.data.cameras.new("cam")
    cam.lens = 32
    cam.clip_end = 3000
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    sc.camera = co
    aim = bpy.data.objects.new("aim", None)
    sc.collection.objects.link(aim)
    tr = co.constraints.new("TRACK_TO")
    tr.target = aim
    tr.track_axis, tr.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"

    def at(yaw_deg, dist, height):
        a = math.radians(yaw_deg)
        return (dist * math.sin(a), -dist * math.cos(a), height)

    now_top = form_z(lifts_now) + 3 * LIFT
    first = plan["pours"][0] if plan["pours"] else None
    r0, r1 = acts["replay"]
    keys = [
        (0.0, at(-25, 230, 120), (0, 0, 14)),
        (2.4, at(-15, 120, 32), (0, 0, 20)),
        (acts["site"][0] - 0.2, at(-4, 104, 26), (0, 0, 20)),
        (acts["site"][1] + 0.3, VIEW, (14, 0, 6)),
    ]
    if first:   # in close for the first pour, keeping left of the lamp post
        keys += [(first["t0"] - 0.3, at(10, 60, 7), (2, 0, 7)),
                 (first["t1"] + 0.2, at(6, 58, 9), (-4, 0, 8))]
    # then wide round the lamp post to the right for the last days: the spoil pile and its excavator in view
    mid = now_top * 0.45
    keys += [(r1 - 7.5, at(8, 64, mid + 3), (0, 0, mid)),
             (r1 - 6.2, at(30, 90, mid + 5), (8, -4, mid)),
             (r1 - 4.8, at(46, 80, mid + 7), (18, -9, mid)),
             (r1 - 0.3, at(48, 84, mid + 9), (20, -10, mid + 1)),
             (proj["bowl"][0], at(-14, 108, 24), (0, 0, top * 0.55)),
             (proj["solid"][0], at(-2, 104, 30), (0, 0, top * 0.62)),
             (acts["orbit"][0] + 1.5, at(12, 98, 28), (0, 0, top * 0.66)),
             (acts["orbit"][1], at(70, 110, 46), (0, 0, top * 0.66))]
    for t, loc, tgt in keys:
        key(co, "location", t, loc)
        key(aim, "location", t, tgt)
    # through the orbit the camera keeps going round: interpolate the angle, not the position
    o0 = acts["orbit"][0] + 1.5
    n = 10
    for i in range(1, n):
        u = i / n
        t = o0 + (acts["orbit"][1] - o0) * u
        key(co, "location", t, at(12 + 58 * u, 98 + 12 * u, 28 + 18 * u))
    key(cam, "shift_x", proj["ghost"][0] + 0.4, 0.0)
    key(cam, "shift_x", proj["ghost"][0] + 1.4, -0.17)


def main():
    a = args()
    plan = a["plan"]
    if plan is None:
        raise SystemExit("--plan PLAN.json is required (intro.py writes it)")
    sc = build(plan, a["draft"])
    if a["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=a["save"])
    if a["out"]:
        import os
        os.makedirs(a["out"], exist_ok=True)
        if a["still"] is not None:
            sc.frame_set(f(a["still"]))
            sc.render.filepath = os.path.join(a["out"], f"still_{a['still']:05.1f}.png")
            bpy.ops.render.render(write_still=True, scene=sc.name)
        else:
            sc.render.filepath = os.path.join(a["out"], "f_")
            bpy.ops.render.render(animation=True, scene=sc.name)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
