#!/usr/bin/env python3
"""Render an assembled wall-on-tile: base + floor + wall, placed in tile space.

Run from a directory holding base.stl, floor.stl and wall.stl:

    AB_ENTRYPOINT=python bin/ab /path/to/bin/assemble-tile.py out.png [--samples 256]

Tile space: base footprint (0,0)-(50.8,50.8), base top at z=6. The floor
(authored 50.8 x 38.1 at y 12.7..50.8, z 0) leaves a 12.7mm strip at y<12.7
for the wall (authored centred on the origin, z from 6). --wall/--floor/--base
are the placements, not deltas. The assembly is spun 180 degrees so the wall
stands at the back. See docs/wall-on-tile-render.md.
"""
import argparse, math, os, sys
import bpy
from mathutils import Vector, Matrix
from autoblender import shot as S

p = argparse.ArgumentParser()
p.add_argument("out")
p.add_argument("--wall", default="25.4,6.35,0")
p.add_argument("--floor", default="0,0,6.0")
p.add_argument("--base", default="0,0,0")
p.add_argument("--yaw", type=float, default=28.0)
p.add_argument("--pitch", type=float, default=24.0)
p.add_argument("--top", action="store_true", help="orthographic straight-down view")
p.add_argument("--samples", type=int, default=24)
p.add_argument("--res", type=int, default=1000)
p.add_argument("--light", type=float, default=1.0, help="stone brightness multiplier")
p.add_argument("--mottle", type=float, default=0.15, help="0 = flat colour, 1 = black blotches")
p.add_argument("--grout-mm", type=float, default=2.5, help="AO reach; ~grout depth")
p.add_argument("--grout-lo", type=float, default=0.5, help="AO at/below this is full grout colour")
p.add_argument("--grout-hi", type=float, default=0.92, help="AO at/above this is clean stone")
p.add_argument("--drybrush", type=float, default=0.0, help="edge highlight strength, 0 = off")
p.add_argument("--db-edge", type=float, default=0.50, help="pointiness where the brush starts to catch")
p.add_argument("--db-width", type=float, default=0.03, help="pointiness ramp width; wider = more coverage")
p.add_argument("--db-break", type=float, default=0.5, help="0 = even highlight, 1 = very patchy")
p.add_argument("--db-min-z", type=float, default=7.6, help="no drybrush below this world z (floor slab edge)")
p.add_argument("--fit", type=float, default=1.05, help="bounding-sphere framing; <1 crops")
p.add_argument("--join", help="also write a manifold union of all three to this STL")
a = p.parse_args(sys.argv[1:])
vec = lambda s: Vector(tuple(float(v) for v in s.split(",")))

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = a.samples
sc.cycles.use_denoising = True
sc.render.resolution_x = sc.render.resolution_y = a.res
sc.view_settings.view_transform = S.VIEW_TRANSFORM
sc.view_settings.look = S.LOOK


STONE = (0.46, 0.40, 0.31)     # base stone colour (linear), before --light
GROUT = (0.018, 0.015, 0.012)
DRYBRUSH = (0.85, 0.80, 0.68)   # pale bone highlight


def _math(nt, op, x, y):
    n = S._new(nt, "ShaderNodeMath")
    n.operation = op
    for sock, v in zip(n.inputs, (x, y)):
        if isinstance(v, float):
            sock.default_value = v
        else:
            nt.links.new(v, sock)
    return n.outputs[0]


def _drybrush(nt, colour):
    """Pale paint on convex edges, biased to up-facing surfaces, broken by noise.

    Not used on the base: it is ~3k faces, so its pointiness smears across whole
    faces and the frame turns uniformly pale.

    Pointiness is Cycles' per-vertex curvature (0.5 = flat, higher = convex); these
    meshes are dense enough for it to follow the stone edges.
    """
    geo = S._new(nt, "ShaderNodeNewGeometry")
    edge = S._ramp(nt, [(a.db_edge, (0, 0, 0)), (a.db_edge + a.db_width, (1, 1, 1))])
    nt.links.new(geo.outputs["Pointiness"], S._in(edge, "Fac", "Factor"))
    # a brush dragged across the top catches up-facing surfaces more: 0.5 side, 1 top
    nz = S._new(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Normal"], nz.inputs[0])
    up = _math(nt, "MULTIPLY_ADD", nz.outputs["Z"], 0.5)
    up.node.inputs[2].default_value = 0.5
    # a nearly dry brush leaves patchy coverage
    grain = S._new(nt, "ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 90.0
    grain.inputs["Detail"].default_value = 8.0
    patch = S._ramp(nt, [(0.5 - a.db_break / 2, (0, 0, 0)), (0.5 + 0.01, (1, 1, 1))])
    nt.links.new(grain.outputs["Fac"], S._in(patch, "Fac", "Factor"))
    patchy = _math(nt, "MAXIMUM", patch.outputs["Color"], 1.0 - a.db_break)
    fac = _math(nt, "MULTIPLY", edge.outputs["Color"], up)
    fac = _math(nt, "MULTIPLY", fac, patchy)
    # The floor's bottom 1.5mm is a square-cut slab of big triangles; pointiness
    # smears across it into a pale band. A brush from above never reaches it anyway.
    # ponytail: tile-space z for this build (base top 6 + slab 1.6); other builds
    # with a different base height need --db-min-z.
    zsep = S._new(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], zsep.inputs[0])
    zmap = S._new(nt, "ShaderNodeMapRange")
    zmap.inputs["From Min"].default_value = a.db_min_z - 0.2
    zmap.inputs["From Max"].default_value = a.db_min_z + 0.2
    nt.links.new(zsep.outputs["Z"], zmap.inputs["Value"])
    fac = _math(nt, "MULTIPLY", fac, zmap.outputs["Result"])
    fac = _math(nt, "MINIMUM", _math(nt, "MULTIPLY", fac, float(a.drybrush)), 1.0)
    mix = S._new(nt, "ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(fac, mix.inputs["Factor"])
    nt.links.new(colour, mix.inputs["A"])
    mix.inputs["B"].default_value = (*DRYBRUSH, 1.0)
    return mix.outputs["Result"]


def stone(name, brush=True):
    """Light stone, mild mottling, dark grout from ambient occlusion.

    The grout lines are real recesses in the mesh, so AO finds them without UVs.
    """
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    col = tuple(min(c * a.light, 1.0) for c in STONE)
    lo = tuple(c * (1 - a.mottle) for c in col)
    noise = S._new(nt, "ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 20.0
    noise.inputs["Detail"].default_value = 3.0
    tone = S._ramp(nt, [(0.35, lo), (0.65, col)])
    nt.links.new(noise.outputs["Fac"], S._in(tone, "Fac", "Factor"))
    ao = S._new(nt, "ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = a.grout_mm
    ao.only_local = True
    cav = S._ramp(nt, [(a.grout_lo, GROUT), (a.grout_hi, (1, 1, 1))])
    nt.links.new(ao.outputs["AO"], S._in(cav, "Fac", "Factor"))
    mul = S._new(nt, "ShaderNodeMix")
    mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    nt.links.new(tone.outputs["Color"], mul.inputs["A"])
    nt.links.new(cav.outputs["Color"], mul.inputs["B"])
    colour = mul.outputs["Result"]
    if a.drybrush and brush:
        colour = _drybrush(nt, colour)
    nt.links.new(colour, b.inputs["Base Color"])
    S._set(b, "Roughness", 0.85)
    bump = S._new(nt, "ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    fine = S._new(nt, "ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 180.0
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


# spin 180 about the tile centre so the wall (tile y<12.7) ends up at the back
spin = (Matrix.Translation((25.4, 25.4, 0)) @ Matrix.Rotation(math.pi, 4, "Z")
        @ Matrix.Translation((-25.4, -25.4, 0)))
parts = {}
for name, off, mat in (("base", a.base, stone("base", brush=False)),
                       ("floor", a.floor, stone("floor")),
                       ("wall", a.wall, stone("wall"))):
    before = set(bpy.data.objects)
    bpy.ops.wm.stl_import(filepath=f"{name}.stl")
    o = (set(bpy.data.objects) - before).pop()
    o.name = name
    o.matrix_world = spin @ Matrix.Translation(vec(off))
    o.data.materials.append(mat)
    parts[name] = o
bpy.context.view_layer.update()

if a.join:
    import numpy as np, trimesh
    from manifold3d import Manifold, Mesh, OpType
    ms = []
    for o in parts.values():
        me = o.data
        v = np.array([(o.matrix_world @ x.co)[:] for x in me.vertices], np.float32)
        me.calc_loop_triangles()
        t = np.array([lt.vertices[:] for lt in me.loop_triangles], np.uint32)
        ms.append(Manifold(Mesh(vert_properties=v, tri_verts=t)))
    j = Manifold.batch_boolean(ms, OpType.Add)
    jm = j.to_mesh()
    trimesh.Trimesh(jm.vert_properties[:, :3], jm.tri_verts).export(a.join)
    print(f"# joined -> {a.join}: {j.num_tri()} tris, genus {j.genus()}, "
          f"vol {j.volume():.0f} (parts sum {sum(m.volume() for m in ms):.0f})")

cs = [o.matrix_world @ Vector(c) for o in parts.values() for c in o.bound_box]
mn = Vector([min(c[i] for c in cs) for i in range(3)])
mx = Vector([max(c[i] for c in cs) for i in range(3)])
center = (mn + mx) / 2

# lighter backdrop: near-white pool fading to light grey, not shot.py's dark corners
S.BG_COLOR, S.BG_TOP, S.VIG_OUTER = (0.95, 0.95, 0.96), (0.62, 0.63, 0.66), 260.0
S._sweep().location = (center.x, center.y, 0)
if a.top:
    cam = bpy.data.cameras.new("Camera")
    cam.type = "ORTHO"
    cam.ortho_scale = max(mx.x - mn.x, mx.y - mn.y) * 1.15
    co = bpy.data.objects.new("Camera", cam)
    bpy.context.collection.objects.link(co)
    co.location = (center.x, center.y, 300)
    sc.camera = co
else:
    S.CAM_YAW, S.CAM_PITCH = a.yaw, a.pitch
    radius = (mx - mn).length / 2 * a.fit
    S._frame_camera(center, radius)
for nm, spec in (("key", S.KEY), ("fill", S.FILL), ("rim", S.RIM)):
    S._light(spec, nm, center)
w = bpy.data.worlds.new("w")
sc.world = w
w.use_nodes = True
w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.55, 0.56, 1)
w.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.35

sc.render.filepath = os.path.abspath(a.out)
bpy.ops.render.render(write_still=True)
print(f"# rendered {a.out}  bbox {tuple(round(v,2) for v in mn)} .. {tuple(round(v,2) for v in mx)}")
