"""Studio product shot of a tile STL, built entirely in code.

No .blend, no image textures, no UVs. Scene, lights, camera, materials and
compositing are all constructed procedurally so a shot reproduces from one
command and every knob is a constant below.
"""

import math

import bpy
from mathutils import Vector

# ---------------------------------------------------------------- parameters
RES = 1000
EXPOSURE = 0.0
VIEW_TRANSFORM = "Standard"   # AgX desaturates hard; this shot wants saturation
LOOK = "Medium High Contrast"
SAMPLES_PREVIEW = 24
SAMPLES_FINAL = 256

# camera: spherical placement around the subject
CAM_YAW = 22.0          # degrees off dead-on; +ve swings right side into view
CAM_PITCH = 9.0        # degrees above horizontal, looking down
CAM_LENS = 85.0         # mm, long-ish for mild perspective
CAM_FILL = 0.98         # fraction of frame height the subject should occupy
CAM_TARGET_BIAS = 0.0  # nudge look-at point up as fraction of height

# lights: (dir_yaw, dir_pitch, distance, size, power, colour)
KEY = dict(yaw=-40.0, pitch=38.0, dist=400.0, size=190.0, power=760000.0,
           color=(1.0, 0.90, 0.76))
FILL = dict(yaw=55.0, pitch=6.0, dist=430.0, size=600.0, power=200000.0,
            color=(0.96, 0.95, 0.97))
RIM = dict(yaw=150.0, pitch=30.0, dist=360.0, size=220.0, power=540000.0,
           color=(1.0, 0.78, 0.55))
# washes the sweep behind the subject so the backdrop reads as a lit gradient
BGLIGHT = dict(yaw=-12.0, pitch=20.0, dist=300.0, size=700.0, power=260000.0,
               color=(1.0, 0.955, 0.90))

# backdrop sweep
BG_COLOR = (0.88, 0.850, 0.780)
BG_TOP = (0.26, 0.244, 0.216)
BG_FLOOR_ROUGH = 0.55
SWEEP_DEPTH = 900.0
SWEEP_HEIGHT = 700.0
SWEEP_RADIUS = 260.0
SWEEP_WIDTH = 1400.0
VIG_CENTER_Z = 40.0     # vignette centred just behind the subject
VIG_INNER = 35.0        # bright pool radius
VIG_OUTER = 155.0       # fully dark by here

# material split. The emblem is only a raised outline stroke (482 faces); its
# interior is plain wall geometry, so the orange field comes from a flood fill.
EMBLEM_Y_MAX = -6.52    # face centres below this are the raised rim stroke
MASK_GRID = 768         # resolution of the XZ raster used for the flood fill
MASK_DILATE = 2         # stroke dilation passes, to close rasterisation gaps
CLOSE_MM = 0.95         # morphological closing that fills the line-art petals
FRONT_Y_MAX = -4.0      # and only within this depth of the front face
RIM_LO, RIM_HI = 0.16, 0.40      # attribute band where the gold rim sits
FIELD_LO, FIELD_HI = 0.60, 0.84  # above this the surface is the orange field
MASK_BLUR = 7                    # box-blur passes softening the mask edge
FLAME_ATTR = "flamecol"          # colour attribute carrying the flame mask

STONE_LIGHT = (0.215, 0.170, 0.104)
STONE_DARK = (0.024, 0.018, 0.012)
STONE_OCHRE = (0.230, 0.112, 0.030)
STONE_ROUGH = (0.62, 0.88)
STONE_NOISE_SCALE = 26.0
STONE_BEVEL = 0.10

EMBLEM_FIELD = (0.530, 0.050, 0.008)
EMBLEM_FIELD_HOT = (0.790, 0.175, 0.024)
EMBLEM_RIM = (0.880, 0.520, 0.180)
EMBLEM_ROUGH = 0.28
EMBLEM_METALLIC = 0.70
EMBLEM_CRACKLE_SCALE = 13.0

METAL_COLOR = (0.055, 0.052, 0.048)
METAL_ROUGH = 0.42
METAL_STIPPLE = 90.0

# plinth, modelled in code (mm). Sits under the wall, slightly proud of it.
PLINTH_OVERHANG = 1.6
PLINTH_HEIGHT = 9.0
PLINTH_SLOT = (20.0, 3.4, 3.0)   # width, height, depth of the front recess

# Blender 5.x scene compositing is a node group; wiring one in blanked the
# render here, so bloom/vignette stay off until that is solved.
POST = False
DEBUG_ATTR = False
GLARE_THRESHOLD = 0.75
GLARE_STRENGTH = 0.35
GLARE_SIZE = 7.0
VIGNETTE = 0.55


# ------------------------------------------------------------------- helpers
def _sph(yaw_deg, pitch_deg, dist, center):
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return Vector((
        center.x + dist * math.cos(p) * math.sin(y),
        center.y - dist * math.cos(p) * math.cos(y),
        center.z + dist * math.sin(p),
    ))


def _aim(obj, target):
    d = target - obj.location
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def _set(node, name, value):
    """Set a socket by name, tolerating Principled socket renames across versions."""
    if name in node.inputs:
        node.inputs[name].default_value = value
        return True
    return False


def _in(node, *names):
    """First socket matching any of these names -- shields against API renames."""
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    raise KeyError(f"{node.bl_idname}: none of {names} in {[i.name for i in node.inputs]}")


def _out(node, *names):
    """First output socket matching any of these names. The Attribute node calls
    its scalar output 'Factor', not 'Fac'."""
    for n in names:
        if n in node.outputs:
            return node.outputs[n]
    raise KeyError(f"{node.bl_idname}: none of {names} in {[o.name for o in node.outputs]}")


def _new(nt, kind, loc=(0, 0)):
    n = nt.nodes.new(kind)
    n.location = loc
    return n


def _ramp(nt, stops, loc=(0, 0)):
    n = _new(nt, "ShaderNodeValToRGB", loc)
    el = n.color_ramp.elements
    while len(el) > 1:
        el.remove(el[-1])
    el[0].position, el[0].color = stops[0][0], (*stops[0][1], 1.0)
    for pos, col in stops[1:]:
        e = el.new(pos)
        e.color = (*col, 1.0)
    return n


# ----------------------------------------------------------------- materials
def _stone_bsdf(nt):
    bsdf = _new(nt, "ShaderNodeBsdfPrincipled", (0, 600))
    geo = _new(nt, "ShaderNodeNewGeometry", (-1200, 300))

    # large-scale mottling between two greys
    noise = _new(nt, "ShaderNodeTexNoise", (-1000, 480))
    noise.inputs["Scale"].default_value = STONE_NOISE_SCALE
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.62
    base = _ramp(nt, [(0.38, STONE_DARK), (0.62, STONE_LIGHT)], (-780, 480))
    nt.links.new(noise.outputs["Fac"], _in(base, "Fac", "Factor"))

    # crevices: cavity darkening + warm ochre where light cannot reach
    ao = _new(nt, "ShaderNodeAmbientOcclusion", (-1000, 120))
    ao.inputs["Distance"].default_value = 4.5
    ao.only_local = True
    ao_ramp = _ramp(nt, [(0.0, (0, 0, 0)), (0.24, (1, 1, 1))], (-780, 120))
    nt.links.new(ao.outputs["AO"], _in(ao_ramp, "Fac", "Factor"))

    ochre = _new(nt, "ShaderNodeMixRGB", (-540, 300))
    ochre.blend_type = "MIX"
    _in(ochre, "Color2", "B").default_value = (*STONE_OCHRE, 1.0)
    nt.links.new(ao_ramp.outputs["Color"], _in(ochre, "Color1", "A"))
    nt.links.new(base.outputs["Color"], _in(ochre, "Color1", "A"))
    mixdark = _new(nt, "ShaderNodeMixRGB", (-360, 300))
    mixdark.blend_type = "MULTIPLY"
    _in(mixdark, "Factor", "Fac").default_value = 1.0
    nt.links.new(base.outputs["Color"], _in(mixdark, "Color1", "A"))
    nt.links.new(ao_ramp.outputs["Color"], _in(mixdark, "Color2", "B"))

    warm = _new(nt, "ShaderNodeMixRGB", (-200, 300))
    warm.blend_type = "MIX"
    _in(warm, "Color2", "B").default_value = (*STONE_OCHRE, 1.0)
    inv = _new(nt, "ShaderNodeInvert", (-360, 60))
    nt.links.new(ao_ramp.outputs["Color"], inv.inputs["Color"])
    fac = _new(nt, "ShaderNodeMath", (-360, -80))
    fac.operation = "MULTIPLY"
    fac.inputs[1].default_value = 0.72
    nt.links.new(inv.outputs["Color"], fac.inputs[0])
    nt.links.new(fac.outputs["Value"], _in(warm, "Factor", "Fac"))
    nt.links.new(mixdark.outputs["Color"], _in(warm, "Color1", "A"))
    nt.links.new(warm.outputs["Color"], bsdf.inputs["Base Color"])

    # edge catch-light: pointiness lifts exposed block corners
    pnt = _ramp(nt, [(0.58, (0, 0, 0)), (0.72, (1, 1, 1))], (-780, -180))
    nt.links.new(geo.outputs["Pointiness"], _in(pnt, "Fac", "Factor"))
    edge = _new(nt, "ShaderNodeMixRGB", (-200, 120))
    edge.blend_type = "SCREEN"
    _in(edge, "Factor", "Fac").default_value = 0.22
    _in(edge, "Color2", "B").default_value = (0.34, 0.29, 0.20, 1.0)
    nt.links.new(warm.outputs["Color"], _in(edge, "Color1", "A"))
    nt.links.new(pnt.outputs["Color"], _in(edge, "Factor", "Fac"))
    nt.links.new(edge.outputs["Color"], bsdf.inputs["Base Color"])

    rough = _ramp(nt, [(0.0, (STONE_ROUGH[1],) * 3), (1.0, (STONE_ROUGH[0],) * 3)],
                  (-540, -340))
    nt.links.new(noise.outputs["Fac"], _in(rough, "Fac", "Factor"))
    nt.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])

    # fake rounded edges so blocks catch a highlight without real bevel geometry
    bev = _new(nt, "ShaderNodeBevel", (-540, -520))
    bev.inputs["Radius"].default_value = STONE_BEVEL
    bev.samples = 8
    nt.links.new(bev.outputs["Normal"], bsdf.inputs["Normal"])

    bump = _new(nt, "ShaderNodeBump", (-360, -640))
    bump.inputs["Strength"].default_value = 0.28
    fine = _new(nt, "ShaderNodeTexNoise", (-540, -700))
    fine.inputs["Scale"].default_value = STONE_NOISE_SCALE * 7.0
    fine.inputs["Detail"].default_value = 6.0
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bev.outputs["Normal"], bump.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return bsdf


def _emblem_bsdf(nt, color, hot, roughness, crackle, loc):
    bsdf = _new(nt, "ShaderNodeBsdfPrincipled", loc)
    if crackle:
        vor = _new(nt, "ShaderNodeTexVoronoi", (-900, 300))
        vor.feature = "DISTANCE_TO_EDGE"
        vor.inputs["Scale"].default_value = EMBLEM_CRACKLE_SCALE
        cr = _ramp(nt, [(0.0, (0, 0, 0)), (0.030, (1, 1, 1))], (-700, 300))
        nt.links.new(vor.outputs["Distance"], _in(cr, "Fac", "Factor"))
        mix = _new(nt, "ShaderNodeMixRGB", (-460, 300))
        mix.blend_type = "MIX"
        _in(mix, "Color1", "A").default_value = (*color, 1.0)
        _in(mix, "Color2", "B").default_value = (*hot, 1.0)
        nt.links.new(cr.outputs["Color"], _in(mix, "Factor", "Fac"))
        nt.links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
        bump = _new(nt, "ShaderNodeBump", (-260, 60))
        bump.inputs["Strength"].default_value = 0.07
        nt.links.new(cr.outputs["Color"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        _set(bsdf, "Base Color", (*color, 1.0))
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", EMBLEM_METALLIC)
    return bsdf


def _wall_material():
    """Stone, orange field and gold rim in one network, split by the 'flame'
    vertex attribute. The rim is the attribute's transition band, so it tracks the
    silhouette exactly instead of being a jagged per-face selection."""
    mat = bpy.data.materials.new("wall")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.bl_idname != "ShaderNodeOutputMaterial":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")

    if DEBUG_ATTR:                      # show the raw attribute to prove it arrives
        a = _new(nt, "ShaderNodeVertexColor", (-400, 0))
        a.layer_name = FLAME_ATTR
        em = _new(nt, "ShaderNodeEmission", (-200, 0))
        nt.links.new(_out(a, "Color", "Factor"), em.inputs["Color"])
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        return mat

    stone = _stone_bsdf(nt)
    field = _emblem_bsdf(nt, EMBLEM_FIELD, EMBLEM_FIELD_HOT, EMBLEM_ROUGH, True, (0, 0))
    rim = _emblem_bsdf(nt, EMBLEM_RIM, EMBLEM_RIM, EMBLEM_ROUGH * 0.8, False, (0, -400))

    attr = _new(nt, "ShaderNodeVertexColor", (-600, -700))
    attr.layer_name = FLAME_ATTR
    # gold occupies the narrow band where the attribute crosses the silhouette
    r_rim = _ramp(nt, [(RIM_LO, (0, 0, 0)), (RIM_HI, (1, 1, 1))], (-400, -800))
    r_fld = _ramp(nt, [(FIELD_LO, (0, 0, 0)), (FIELD_HI, (1, 1, 1))], (-400, -1000))
    nt.links.new(_out(attr, "Color", "Factor"), _in(r_rim, "Fac", "Factor"))
    nt.links.new(_out(attr, "Color", "Factor"), _in(r_fld, "Fac", "Factor"))

    mix1 = _new(nt, "ShaderNodeMixShader", (300, 200))    # stone -> gold
    nt.links.new(r_rim.outputs["Color"], mix1.inputs[0])
    nt.links.new(stone.outputs[0], mix1.inputs[1])
    nt.links.new(rim.outputs[0], mix1.inputs[2])

    mix2 = _new(nt, "ShaderNodeMixShader", (500, 100))    # ...then -> orange
    nt.links.new(r_fld.outputs["Color"], mix2.inputs[0])
    nt.links.new(mix1.outputs[0], mix2.inputs[1])
    nt.links.new(field.outputs[0], mix2.inputs[2])
    nt.links.new(mix2.outputs[0], out.inputs["Surface"])
    return mat


def _metal_material():
    mat = bpy.data.materials.new("metal")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    _set(bsdf, "Base Color", (*METAL_COLOR, 1.0))
    _set(bsdf, "Roughness", METAL_ROUGH)
    _set(bsdf, "Metallic", 0.30)
    stip = _new(nt, "ShaderNodeTexNoise", (-600, -100))
    stip.inputs["Scale"].default_value = METAL_STIPPLE
    stip.inputs["Detail"].default_value = 4.0
    bump = _new(nt, "ShaderNodeBump", (-360, -100))
    bump.inputs["Strength"].default_value = 0.22
    nt.links.new(stip.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


# ---------------------------------------------------------------- geometry
def _flame_mask(me, raised, grid=MASK_GRID):
    """Region enclosed by the raised emblem outline.

    The emblem is only a raised stroke -- its interior is ordinary wall geometry
    that the reference simply paints. So rasterise the stroke in XZ, dilate to
    close rasterisation gaps, flood fill inward from the border, and whatever the
    fill cannot reach is inside the flame.
    """
    import numpy as np

    co = np.empty((len(me.vertices), 3))
    me.vertices.foreach_get("co", co.ravel())
    x0, x1 = co[:, 0].min(), co[:, 0].max()
    z0, z1 = co[:, 2].min(), co[:, 2].max()

    # Rasterise the stroke by walking every triangle edge. Plotting vertices
    # alone leaves gaps that the flood fill escapes through.
    gx = (co[:, 0] - x0) / (x1 - x0) * (grid - 1)
    gz = (co[:, 2] - z0) / (z1 - z0) * (grid - 1)
    stroke = np.zeros((grid, grid), bool)
    for pi in raised:
        vs = list(me.polygons[pi].vertices)
        for a, b in zip(vs, vs[1:] + vs[:1]):
            n_s = int(max(abs(gx[a] - gx[b]), abs(gz[a] - gz[b])) * 2) + 2
            t = np.linspace(0.0, 1.0, n_s)
            xi = np.clip((gx[a] + (gx[b] - gx[a]) * t).astype(int), 0, grid - 1)
            zi = np.clip((gz[a] + (gz[b] - gz[a]) * t).astype(int), 0, grid - 1)
            stroke[zi, xi] = True
    def dilate(a, k):
        for _ in range(k):
            d = a.copy()
            d[1:, :] |= a[:-1, :]
            d[:-1, :] |= a[1:, :]
            d[:, 1:] |= a[:, :-1]
            d[:, :-1] |= a[:, 1:]
            a = d
        return a

    def erode(a, k):
        for _ in range(k):
            e = a.copy()
            e[1:, :] &= a[:-1, :]
            e[:-1, :] &= a[1:, :]
            e[:, 1:] &= a[:, :-1]
            e[:, :-1] &= a[:, 1:]
            a = e
        return a

    def outside(st):
        free = ~st
        r = np.zeros_like(st)
        r[0, :] = free[0, :]
        r[-1, :] = free[-1, :]
        r[:, 0] = free[:, 0]
        r[:, -1] = free[:, -1]
        while True:
            d = r.copy()
            d[1:, :] |= r[:-1, :]
            d[:-1, :] |= r[1:, :]
            d[:, 1:] |= r[:, :-1]
            d[:, :-1] |= r[:, 1:]
            d &= free
            if (d == r).all():
                return r
            r = d

    stroke = dilate(stroke, MASK_DILATE)
    # The emblem is line art: the petal outlines have gaps, so a plain flood fill
    # leaks in and only the ribbon itself gets filled. Close the gaps first, fill,
    # then erode back to recover the true silhouette.
    k = max(int(CLOSE_MM / ((x1 - x0) / (grid - 1))), 1)
    closed = dilate(stroke, k)
    solid = ~outside(closed) & ~closed
    flame = erode(solid | closed, k) | stroke
    return flame, (x0, x1, z0, z1)


def _paint_flame(obj):
    """Write a smooth 0..1 'flame' attribute per vertex.

    Per-face material assignment sawtooths badly here: the flame boundary cuts
    through triangles that are coarse relative to the edge, so each face flips
    wholesale. A per-vertex attribute interpolates across faces instead, giving a
    sub-face edge -- and its transition band is exactly where the gold rim goes.
    """
    import numpy as np

    me = obj.data
    n = len(me.polygons)
    centers = np.empty((n, 3))
    normals = np.empty((n, 3))
    me.polygons.foreach_get("center", centers.ravel())
    me.polygons.foreach_get("normal", normals.ravel())
    raised = np.flatnonzero((centers[:, 1] < EMBLEM_Y_MAX) & (normals[:, 1] < 0.0))
    flame, (x0, x1, z0, z1) = _flame_mask(me, raised)

    # soften the hard mask into a gradient so the sampled edge is not stair-stepped
    f = flame.astype(np.float32)
    for _ in range(MASK_BLUR):
        f = (f
             + np.roll(f, 1, 0) + np.roll(f, -1, 0)
             + np.roll(f, 1, 1) + np.roll(f, -1, 1)) / 5.0
    grid = f.shape[0]

    co = np.empty((len(me.vertices), 3))
    me.vertices.foreach_get("co", co.ravel())
    fx = np.clip((co[:, 0] - x0) / (x1 - x0) * (grid - 1), 0, grid - 1.001)
    fz = np.clip((co[:, 2] - z0) / (z1 - z0) * (grid - 1), 0, grid - 1.001)
    ix, iz = fx.astype(int), fz.astype(int)
    tx, tz = fx - ix, fz - iz
    val = (f[iz, ix] * (1 - tx) * (1 - tz) + f[iz, ix + 1] * tx * (1 - tz)
           + f[iz + 1, ix] * (1 - tx) * tz + f[iz + 1, ix + 1] * tx * tz)
    # only the front face carries the emblem; kill it on sides, back and slots
    val = np.where(co[:, 1] < FRONT_Y_MAX, val, 0.0)

    # A plain FLOAT attribute reads back correctly from Python but arrives at
    # Cycles as zero in this build; a FLOAT_COLOR colour attribute does reach the
    # shader, so carry the mask in RGB.
    ca = me.color_attributes.new(FLAME_ATTR, "FLOAT_COLOR", "POINT")
    cols = np.zeros((len(me.vertices), 4), np.float32)
    cols[:, :3] = val.astype(np.float32)[:, None]
    cols[:, 3] = 1.0
    ca.data.foreach_set("color", cols.ravel().tolist())
    me.update()
    return int((val > 0.5).sum()), len(val)


def _plinth(wall_min, wall_max, metal):
    w = wall_max.x - wall_min.x
    d = wall_max.y - wall_min.y
    cx = (wall_max.x + wall_min.x) / 2
    cy = (wall_max.y + wall_min.y) / 2
    hw = w / 2 + PLINTH_OVERHANG
    hd = d / 2 + PLINTH_OVERHANG
    bpy.ops.mesh.primitive_cube_add(size=1, location=(cx, cy, PLINTH_HEIGHT / 2))
    base = bpy.context.active_object
    base.name = "plinth"
    base.scale = (hw * 2, hd * 2, PLINTH_HEIGHT)
    bpy.ops.object.transform_apply(scale=True)
    base.data.materials.append(metal)   # before the boolean, so slots survive it

    sw, sh, sd = PLINTH_SLOT
    bpy.ops.mesh.primitive_cube_add(
        size=1, location=(cx, wall_min.y - PLINTH_OVERHANG - sd / 2 + 0.4,
                          PLINTH_HEIGHT * 0.45))
    cut = bpy.context.active_object
    cut.scale = (sw, sd * 2, sh)
    bpy.ops.object.transform_apply(scale=True)
    m = base.modifiers.new("slot", "BOOLEAN")
    m.operation = "DIFFERENCE"
    m.object = cut
    m.solver = "EXACT"
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cut, do_unlink=True)
    if not base.data.materials:
        base.data.materials.append(metal)
    for p in base.data.polygons:
        p.material_index = 0
    return base


def _sweep():
    """Cyclorama: floor running back then arcing up, so there is no horizon line."""
    verts, faces = [], []
    prof = [(-SWEEP_DEPTH * 0.25, 0.0)]
    steps = 48
    for i in range(steps + 1):
        a = math.radians(90.0 * i / steps)
        prof.append((SWEEP_RADIUS * math.sin(a) - 0.0,
                     SWEEP_RADIUS * (1 - math.cos(a))))
    prof.append((SWEEP_RADIUS, SWEEP_HEIGHT))
    for x in (-SWEEP_WIDTH / 2, SWEEP_WIDTH / 2):
        for (y, z) in prof:
            verts.append((x, y, z))
    n = len(prof)
    for i in range(n - 1):
        faces.append((i, i + 1, n + i + 1, n + i))
    me = bpy.data.meshes.new("sweep")
    me.from_pydata(verts, [], faces)
    me.update()
    obj = bpy.data.objects.new("sweep", me)
    bpy.context.collection.objects.link(obj)
    for p in me.polygons:
        p.use_smooth = True
    mat = bpy.data.materials.new("backdrop")
    mat.use_nodes = True
    nt = mat.node_tree
    b = nt.nodes["Principled BSDF"]
    _set(b, "Roughness", BG_FLOOR_ROUGH)
    # Radial falloff around the subject: a bright pool behind it fading to dark
    # corners. This is the vignette, done in-scene because the 5.x compositor
    # blanks the render here.
    tc = _new(nt, "ShaderNodeTexCoord", (-900, 0))
    sub = _new(nt, "ShaderNodeVectorMath", (-720, 0))
    sub.operation = "SUBTRACT"
    sub.inputs[1].default_value = (0.0, 0.0, VIG_CENTER_Z)
    nt.links.new(tc.outputs["Object"], sub.inputs[0])
    ln = _new(nt, "ShaderNodeVectorMath", (-540, 0))
    ln.operation = "LENGTH"
    nt.links.new(sub.outputs["Vector"], ln.inputs[0])
    mapr = _new(nt, "ShaderNodeMapRange", (-380, 0))
    mapr.inputs["From Min"].default_value = VIG_INNER
    mapr.inputs["From Max"].default_value = VIG_OUTER
    nt.links.new(ln.outputs["Value"], mapr.inputs["Value"])
    ramp = _ramp(nt, [(0.0, BG_COLOR), (1.0, BG_TOP)], (-200, 0))
    nt.links.new(mapr.outputs["Result"], _in(ramp, "Fac", "Factor"))
    nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    obj.data.materials.append(mat)
    return obj


def _light(spec, name, center):
    d = bpy.data.lights.new(name, type="AREA")
    d.size = spec["size"]
    d.energy = spec["power"]
    d.color = spec["color"]
    o = bpy.data.objects.new(name, d)
    bpy.context.collection.objects.link(o)
    o.location = _sph(spec["yaw"], spec["pitch"], spec["dist"], center)
    _aim(o, center)
    return o


def _frame_camera(center, radius):
    cam = bpy.data.cameras.new("Camera")
    cam.lens = CAM_LENS
    obj = bpy.data.objects.new("Camera", cam)
    bpy.context.collection.objects.link(obj)
    # distance such that the subject sphere fills CAM_FILL of the frame
    half = math.atan(cam.sensor_width / 2 / cam.lens)
    dist = radius / math.tan(half * CAM_FILL)
    obj.location = _sph(CAM_YAW, CAM_PITCH, dist, center)
    _aim(obj, center)
    bpy.context.scene.camera = obj
    return obj


def _compositor(scene):
    """Bloom on the emblem plus a corner vignette.

    In Blender 5.x every Glare setting is an input socket, not a property.
    """
    nt = bpy.data.node_groups.new("comp", "CompositorNodeTree")
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    # In Blender 5.x the scene compositor is a node group: the render arrives on
    # the group input and must leave on the group output. CompositorNodeComposite
    # no longer exists.
    nt.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    nt.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl = nt.nodes.new("NodeGroupInput")
    out = nt.nodes.new("NodeGroupOutput")
    scene.compositing_node_group = nt

    glare = nt.nodes.new("CompositorNodeGlare")
    for sock, val in (("Type", "BLOOM"), ("Quality", "HIGH"),
                      ("Threshold", GLARE_THRESHOLD), ("Strength", GLARE_STRENGTH),
                      ("Size", GLARE_SIZE)):
        if sock in glare.inputs:
            try:
                glare.inputs[sock].default_value = val
            except (TypeError, AttributeError):
                pass
    nt.links.new(rl.outputs[0], glare.inputs["Image"])
    src = glare.outputs["Image"]

    mix = None
    for idname in ("CompositorNodeMix", "CompositorNodeMixRGB"):
        try:
            mix = nt.nodes.new(idname)
            break
        except RuntimeError:
            continue
    if mix is not None:
        ell = nt.nodes.new("CompositorNodeEllipseMask")
        for sock, val in (("Width", 1.20), ("Height", 1.20)):
            if sock in ell.inputs:
                ell.inputs[sock].default_value = val
        if "Size" in ell.inputs:
            try:
                ell.inputs["Size"].default_value = (1.20, 1.20)
            except (TypeError, ValueError):
                pass
        blur = nt.nodes.new("CompositorNodeBlur")
        if "Size" in blur.inputs:
            blur.inputs["Size"].default_value = 220.0
        nt.links.new(ell.outputs[0], blur.inputs["Image"])
        try:
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
        except (AttributeError, TypeError):
            pass
        a = _in(mix, "A", "Color1")
        b = _in(mix, "B", "Color2")
        _in(mix, "Factor", "Fac").default_value = VIGNETTE
        nt.links.new(src, a)
        nt.links.new(blur.outputs["Image"], b)
        src = mix.outputs[0] if mix.outputs else src
    nt.links.new(src, out.inputs[0])
    return "glare+vignette" if mix is not None else "glare"


def shot(stl_path, out_path, samples=SAMPLES_PREVIEW, res=RES):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.film_transparent = False
    try:
        scene.view_settings.view_transform = VIEW_TRANSFORM
    except TypeError:
        pass
    try:
        scene.view_settings.look = LOOK
    except TypeError:
        pass
    scene.view_settings.exposure = EXPOSURE

    before = set(bpy.data.objects)
    bpy.ops.wm.stl_import(filepath=stl_path)
    wall = (set(bpy.data.objects) - before).pop()
    wall.location.z += PLINTH_HEIGHT          # stand it on the plinth, not in the floor
    bpy.context.view_layer.update()

    cs = [wall.matrix_world @ Vector(c) for c in wall.bound_box]
    mn = Vector((min(c.x for c in cs), min(c.y for c in cs), min(c.z for c in cs)))
    mx = Vector((max(c.x for c in cs), max(c.y for c in cs), max(c.z for c in cs)))

    metal = _metal_material()
    n_field, n_rim = _paint_flame(wall)
    wall.data.materials.clear()
    wall.data.materials.append(_wall_material())

    plinth = _plinth(mn, mx, metal)
    _sweep()

    center = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mx.z / 2))
    center.z += mx.z * CAM_TARGET_BIAS
    # frame from the projected silhouette, not the 3D diagonal, or the subject
    # ends up far too small in frame
    w, d, h = mx.x - mn.x, mx.y - mn.y, mx.z
    yaw = math.radians(CAM_YAW)
    eff_w = w * abs(math.cos(yaw)) + d * abs(math.sin(yaw))
    radius = max(max(eff_w, h) / 2, 1.0)
    _frame_camera(center, radius)
    for nm, spec in (("key", KEY), ("fill", FILL), ("rim", RIM)):
        _light(spec, nm, center)
    bg = _light(BGLIGHT, "bglight", center)
    bg.location.y += 120.0          # push behind the subject, onto the sweep
    _aim(bg, center + Vector((0, 260.0, 0)))

    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.55, 0.56, 1.0)
    bg.inputs["Strength"].default_value = 0.35

    post = "off"
    if POST:
        try:
            post = _compositor(scene)
        except Exception as e:
            print(f"# shot: compositor skipped ({e})")
    scene.render.filepath = out_path
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    pd = plinth.dimensions
    return dict(post=post, plinth=f"{pd.x:.1f}x{pd.y:.1f}x{pd.z:.1f}"
                       f" mat={[m.name if m else None for m in plinth.data.materials]}",
                emblem_field_faces=n_field, emblem_rim_faces=n_rim,
                tris=len(wall.data.polygons), samples=samples, res=res)
