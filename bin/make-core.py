#!/usr/bin/env python3
"""Assemble a core from an openforge tile plus its lockless base.

A core is the wall with a base on it and no locks -- the thing every geometry
node group expects as input.  The openforge variant is that same wall with the
bottom 6mm cut off, so:

    core = openforge tile (lifted back up) + Base-<letter>.<Texture> , remeshed

Two alignment traps, both silent:

  * The openforge tile has usually been repaired, which drops it to z=0.  It has
    to go back up so its cut face meets the top of the base.
  * The tile can be off in Y by a multiple of 15mm (moved by whole grid steps),
    and then off by less than half a step in X or Y (squared on the wall band
    just above the base).

Run it in the bpy container:

    AB_ENTRYPOINT=python bin/ab bin/make-core.py \
        --library .../library.primary.A.blend --texture "Dungeon Stone" \
        --tile .../dungeon_stone#drain.A.openforge.stl --out .../drain.A.core.stl
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

import bpy

GRID = 15.0          # walls are placed on a 15mm grid
NOISE = 1.0          # tile bottom within this of the base top already sits on it


def bounds(obj):
    cs = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return ([min(c[i] for c in cs) for i in range(3)],
            [max(c[i] for c in cs) for i in range(3)])


def band_centre(obj, z0, z1):
    """XY centre of the object's vertices between two heights, world space."""
    cs = [obj.matrix_world @ v.co for v in obj.data.vertices]
    cs = [c for c in cs if z0 < c[2] < z1]
    if not cs:
        return None
    return ((min(c[0] for c in cs) + max(c[0] for c in cs)) / 2,
            (min(c[1] for c in cs) + max(c[1] for c in cs)) / 2)


def flip_matrix(cx, cy):
    """180 degrees about Z, pivoting on (cx, cy)."""
    from mathutils import Matrix
    return (Matrix.Translation((cx, cy, 0.0))
            @ Matrix.Rotation(3.141592653589793, 4, "Z")
            @ Matrix.Translation((-cx, -cy, 0.0)))


def demo():
    """A flip must mirror X and Y about the pivot, leave Z alone, and undo itself.

    Tolerance is 1e-4, not 1e-9: mathutils Vector and Matrix are SINGLE precision,
    so a pivot of 7.5 sends 15.0 back as 14.999995.  That is 5e-6 of slop on a
    mesh whose voxel remesh is 0.1mm, so it is noise -- but a 1e-9 assert here
    fails on correct maths.
    """
    from mathutils import Vector
    tol = 1e-4
    m = flip_matrix(50.0, 7.5)
    assert (m @ Vector((0.0, 0.0, 12.0)) - Vector((100.0, 15.0, 12.0))).length < tol
    assert (m @ Vector((50.0, 7.5, 3.0)) - Vector((50.0, 7.5, 3.0))).length < tol
    assert (m @ (m @ Vector((17.0, 2.0, 9.0))) - Vector((17.0, 2.0, 9.0))).length < tol
    # a rotation, not a mirror: handedness survives, so the part stays printable
    assert m.to_3x3().determinant() > 0
    print("make-core flip: ok")


def find_base(blend, texture):
    """Base object for this texture.  The separator is '.' in most libraries and
    '-' in others (Base-D-Dungeon Stone), so match rather than format a name."""
    pat = re.compile(rf"^Base-[A-Za-z]+[.-]{re.escape(texture)}$")
    with bpy.data.libraries.load(blend, link=False) as (src, dst):
        names = [n for n in src.objects if pat.match(n)]
        if not names:
            cands = [n for n in src.objects if n.startswith("Base-") and texture in n]
            raise SystemExit(f"no lockless base for {texture!r} in {os.path.basename(blend)}"
                             + (f"; closest: {cands[:4]}" if cands else ""))
        dst.objects = [names[0]]
    obj = dst.objects[0]
    bpy.context.scene.collection.objects.link(obj)
    return obj


def square_on_base(tile, blo, bhi):
    """Step 2: move the tile by any residual under half a grid step in X and Y."""
    from mathutils import Vector
    # Sub-millimetre offsets got whole batches rejected, so this always runs.
    # Measure the wall just above the base rather than the tile's whole bounding
    # box: anything protruding in Y higher up (a boss door's skull) drags a
    # full-bbox centre off, and the band does not care.  Cap at half a grid step
    # so this only ever squares the tile -- a genuine grid move is step 1's job.
    wall_c = band_centre(tile, bhi[2] + 2.0, bhi[2] + 6.0)
    if wall_c:
        rx = (blo[0] + bhi[0]) / 2 - wall_c[0]
        ry = (blo[1] + bhi[1]) / 2 - wall_c[1]
        if abs(rx) < GRID / 2 and abs(ry) < GRID / 2:
            print(f"# square dx={rx:+.3f} dy={ry:+.3f}")
            tile.location += Vector((rx, ry, 0.0))
            bpy.context.view_layer.update()
        else:
            print(f"# NOT squaring: residual dx={rx:+.3f} dy={ry:+.3f} "
                  f"exceeds half a grid step -- check the tile")


def main():
    # `--demo` is a self-check and takes no inputs, but the four required
    # arguments below made argparse exit 2 before the branch could run -- the
    # documented invocation only worked if you also passed four dummy paths.
    if "--demo" in sys.argv:
        return demo()

    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--texture", required=True, help='e.g. "Dungeon Stone"')
    ap.add_argument("--tile", required=True, help="the openforge STL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voxel", type=float, default=0.1)
    ap.add_argument("--ratio", type=float, default=0.1)
    ap.add_argument("--no-repair", action="store_true",
                    help="skip the manifold repair pass (for comparison)")
    ap.add_argument("--flip", action="store_true",
                    help="turn the tile end-for-end before the base goes on -- this is "
                         "what a '+reverse' variant is.  The tile is asymmetric along "
                         "its length, so reverse is the same part offered the other way "
                         "round, not a separate sculpt.")
    ap.add_argument("--demo", action="store_true", help="self-check the flip maths")
    a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])

    if a.demo:
        return demo()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    base = find_base(a.library, a.texture)
    bpy.ops.wm.stl_import(filepath=a.tile)
    tile = next((o for o in bpy.data.objects
                 if o.type == "MESH" and o.name != base.name), None)
    if tile is None:
        raise SystemExit(f"no mesh imported from {a.tile} -- empty or unreadable STL")
    bpy.context.view_layer.update()

    blo, bhi = bounds(base)

    if a.flip:
        # Rotate about the BASE's XY centre, not the tile's own, so the flipped
        # tile lands square on it.  Z is untouched by a Z-rotation, so the lift
        # below still reads the right value.  (This comment used to claim X is
        # never corrected downstream; that stopped being true when step 2's
        # squaring was added, which corrects both X and Y.)
        tile.data.transform(flip_matrix((blo[0] + bhi[0]) / 2, (blo[1] + bhi[1]) / 2))
        tile.data.update()
        bpy.context.view_layer.update()
        print("# flipped the tile end-for-end (+reverse)")

    tlo, thi = bounds(tile)
    cut = bhi[2] - blo[2]                       # base height == the cut depth

    from mathutils import Vector

    # Z: if the tile already sits on top of the base, leave it; otherwise lift it.
    dz = 0.0 if abs(tlo[2] - bhi[2]) < NOISE else cut - tlo[2] + blo[2]
    # Y, step 1: a real grid offset is a whole multiple of 15mm.
    dy_raw = ((blo[1] + bhi[1]) - (tlo[1] + thi[1])) / 2
    dy_grid = round(dy_raw / GRID) * GRID

    print(f"# base {base.name!r} h={cut:.3f}  tile z0={tlo[2]:.3f}")
    print(f"# move dz={dz:+.3f} dy_grid={dy_grid:+.3f}  (raw y offset {dy_raw:+.3f})")
    tile.location += Vector((0.0, dy_grid, dz))
    bpy.context.view_layer.update()

    square_on_base(tile, blo, bhi)

    bpy.ops.object.select_all(action="DESELECT")
    base.select_set(True)
    tile.select_set(True)
    bpy.context.view_layer.objects.active = tile
    bpy.ops.object.join()

    for name, kind, setup in (("Remesh", "REMESH", lambda m: (setattr(m, "mode", "VOXEL"),
                                                              setattr(m, "voxel_size", a.voxel),
                                                              setattr(m, "adaptivity", 0.0))),
                              ("Decimate", "DECIMATE", lambda m: (setattr(m, "decimate_type", "COLLAPSE"),
                                                                  setattr(m, "ratio", a.ratio)))):
        mod = tile.modifiers.new(name, kind)
        setup(mod)
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Did the squaring actually land?  Measure the wall band again now that the
    # join and remesh have happened, against the base's own footprint.
    #
    # The reference MUST be the base's full bounding box, the same one the
    # correction used -- NOT a z-band of the finished core.  `Base-D` is not a
    # box: between z=1 and z=5 it is undercut to y=[-0.250, 6.522] while its
    # footprint is y=[-6.350, 6.522].  Measuring that band put its "centre" at
    # +3.136 and reported a perfectly square tile as 1.85-2.25mm out.
    final_c = band_centre(tile, bhi[2] + 2.0, bhi[2] + 6.0)
    if final_c:
        dx = (blo[0] + bhi[0]) / 2 - final_c[0]
        dy = (blo[1] + bhi[1]) / 2 - final_c[1]
        flag = "  <-- CHECK: tile is not square on its base" if max(abs(dx), abs(dy)) > 0.1 else ""
        print(f"# base-vs-wall dx={dx:+.3f} dy={dy:+.3f}{flag}")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    tile.select_set(True)
    bpy.context.view_layer.objects.active = tile
    lo, hi = bounds(tile)

    # remesh+decimate does not give a manifold result -- every core built this
    # way has needed a repair pass.  union.py's shell repair + manifold3d union
    # is the same operation, so do it here instead of by hand.
    fd, raw = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        bpy.ops.wm.stl_export(filepath=raw, export_selected_objects=True)
        if a.no_repair:
            shutil.move(raw, a.out)     # raw is in the container's /tmp: another device
            print(f"# wrote {os.path.basename(a.out)} UNREPAIRED  h={hi[2]-lo[2]:.3f} "
                  f"tris={len(tile.data.polygons)}")
            return
        from autoblender.union import union_stl
        r = union_stl(raw, a.out)
        print(f"# wrote {os.path.basename(a.out)}  h={hi[2]-lo[2]:.3f} "
              f"tris={len(tile.data.polygons)} -> {r['tris']} after repair "
              f"(shells={r['shells']} solids={r['solids']} voids={r['voids']}"
              + (f" LOST {r['lost']:.4f}mm3" if abs(r["lost"]) > 1e-6 else "") + ")")
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


if __name__ == "__main__":
    main()
