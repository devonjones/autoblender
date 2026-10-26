#!/usr/bin/env python3
"""Render an assembled wall-on-tile: base + floor + wall (or a --parts subset, plus any
--add parts such as corner wings), placed in tile space.

Run from a directory holding base.stl, floor.stl and wall.stl:

    AB_ENTRYPOINT=python bin/ab /path/to/bin/assemble-tile.py out.png [--samples 256]

Tile space: base footprint (0,0)-(50.8,50.8), base top at z=6. The floor
(authored 50.8 x 38.1 at y 12.7..50.8, z 0) leaves a 12.7mm strip at y<12.7
for the wall (authored centred on the origin, z from 6). --wall/--floor/--base
are the placements, not deltas. --spin (default 180) turns the assembly so the
wall stands at the back. See docs/wall-on-tile-render.md.
"""
import argparse, math, os, sys
import bpy
from mathutils import Vector, Matrix
from autoblender import shot as S
from autoblender.ops import import_stl

p = argparse.ArgumentParser()
p.add_argument("out")
p.add_argument("--wall", default="25.4,6.35,0")
p.add_argument("--floor", default="0,0,6.0")
p.add_argument("--base", default="0,0,0")
p.add_argument("--parts", default="base,floor,wall", help="which of base,floor,wall to load, e.g. base,floor for a floor-only shot")
p.add_argument("--add", action="append", default=[], metavar="NAME:X,Y,Z[,ROTZ]",
               help="extra drybrushed part NAME.stl, turned ROTZ degrees about its origin then moved; repeatable")
p.add_argument("--wood", default="", help="comma list of part names to paint as wood (e.g. a door), not stone")
p.add_argument("--spin", type=float, default=180.0, help="turn the assembly about the tile centre; 180 puts the wall at the back")
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
p.add_argument("--join", help="also write a manifold union of the loaded parts to this STL")
a = p.parse_args(sys.argv[1:])
a.parts = [n for n in a.parts.split(",") if n]
if not a.parts or set(a.parts) - {"base", "floor", "wall"}:
    p.error(f"--parts takes base, floor and/or wall; got {','.join(a.parts) or 'nothing'}")
if not math.isfinite(a.spin):
    p.error(f"--spin takes a finite angle; got {a.spin}")


def place(flag, spec):
    """x,y,z[,rotz] -> (x, y, z, rotz) floats, or a usage error naming the flag."""
    try:
        v = [float(t) for t in spec.split(",")]
    except ValueError:
        v = []
    if len(v) not in (3, 4) or not all(map(math.isfinite, v)):
        p.error(f"{flag} takes x,y,z[,rotz]; got {spec!r}")
    return (*v, 0.0)[:4]


placed = [(n, place(f"--{n}", getattr(a, n)), n != "base") for n in ("base", "floor", "wall")
          if n in a.parts]
for spec in a.add:
    name, _, off = spec.partition(":")
    if not name or name in [n for n, *_ in placed]:
        p.error(f"--add needs a new part name: {spec!r} clashes or has none")
    placed.append((name, place(f"--add {name}", off), True))

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
WOOD = (0.17, 0.09, 0.04)       # dark stained wood (linear), before --light
WOOD_DB = (0.60, 0.43, 0.26)    # worn tan highlight on the wood's edges


def _math(nt, op, x, y):
    n = S._new(nt, "ShaderNodeMath")
    n.operation = op
    for sock, v in zip(n.inputs, (x, y)):
        if isinstance(v, float):
            sock.default_value = v
        else:
            nt.links.new(v, sock)
    return n.outputs[0]


def _drybrush(nt, colour, hi=DRYBRUSH):
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
    # The floor's bottom ~1.6mm is a square-cut slab of big triangles; pointiness
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
    mix.inputs["B"].default_value = (*hi, 1.0)
    return mix.outputs["Result"]


def stone(name, brush=True, wood=False):
    """Light stone, mild mottling, dark grout from ambient occlusion.

    The grout lines are real recesses in the mesh, so AO finds them without UVs.
    wood=True swaps in a stained-wood palette, with vertical grain from wave bands
    instead of the mottling noise.
    """
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    col = tuple(min(c * a.light, 1.0) for c in (WOOD if wood else STONE))
    lo = tuple(c * (1 - (0.45 if wood else a.mottle)) for c in col)
    if wood:
        noise = S._new(nt, "ShaderNodeTexWave")     # bands across x = grain running up the boards
        noise.wave_type, noise.bands_direction = "BANDS", "X"
        noise.inputs["Scale"].default_value = 6.0
        noise.inputs["Distortion"].default_value = 8.0
        noise.inputs["Detail"].default_value = 4.0
    else:
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
        colour = _drybrush(nt, colour, WOOD_DB if wood else DRYBRUSH)
    nt.links.new(colour, b.inputs["Base Color"])
    S._set(b, "Roughness", 0.85)
    bump = S._new(nt, "ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    fine = S._new(nt, "ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 180.0
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


# --spin about the tile centre; the default 180 puts the wall (tile y<12.7) at the back
spin = (Matrix.Translation((25.4, 25.4, 0)) @ Matrix.Rotation(math.radians(a.spin), 4, "Z")
        @ Matrix.Translation((-25.4, -25.4, 0)))
parts = {}
for name, (x, y, z, rotz), brush in placed:
    mat = stone(name, brush=brush, wood=name in a.wood.split(","))
    o = import_stl(f"{name}.stl")
    o.name = name
    o.matrix_world = (spin @ Matrix.Translation((x, y, z))
                      @ Matrix.Rotation(math.radians(rotz), 4, "Z"))
    o.data.materials.append(mat)
    parts[name] = o
bpy.context.view_layer.update()

if a.join:
    import numpy as np
    from manifold3d import Error, Manifold, Mesh, OpType
    from autoblender.union import to_trimesh
    ms = []
    for name, o in parts.items():
        me = o.data
        v = np.array([(o.matrix_world @ x.co)[:] for x in me.vertices], np.float32)
        me.calc_loop_triangles()
        t = np.array([lt.vertices[:] for lt in me.loop_triangles], np.uint32)
        m = Manifold(Mesh(vert_properties=v, tri_verts=t))
        # a rejected part comes back empty and would silently drop out of the union
        if m.status() != Error.NoError:
            raise RuntimeError(f"--join: manifold3d rejected {name}: {m.status()}")
        ms.append(m)
    j = Manifold.batch_boolean(ms, OpType.Add)
    if j.status() != Error.NoError:
        raise RuntimeError(f"--join: union failed: {j.status()}")
    to_trimesh(j).export(a.join)
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
