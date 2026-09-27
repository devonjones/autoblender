#!/usr/bin/env python3
"""Add side clip ports to a wall, skipping the ones with nothing behind them.

A wall has four side ports -- upper and lower at each end.  Each has a port
block (the positive that guarantees a flat mating surface) and a cutter per lock
style.  Where a wall feature sits in the way, e.g. the drain cavity in the lower
band, cutting the port leaves a gap into that cavity.

So each port is probed first: build a box from its block's bounds, inset 1/8" at
the outer X end (the clip exits there by design, and the stone texture is ragged
at the very edge), and intersect it with the piece.  A port with solid material
behind it fills the box; `drain.IA` fills the upper two at 100% and the lower two
at ~65%.

A red port gets neither block nor cut -- an unused block would leave a flat pad
on the wall for no reason.

    AB_ENTRYPOINT=python bin/ab bin/make-ports.py \
        --library .../library.primary.IA.blend --style dragonbite \
        --piece .../drain.IA.openforge.stl --out .../out.stl
"""
import argparse
import os
import re
import sys

import bpy      # must come first: the bpy module registers bmesh on init,
import bmesh   # so importing bmesh before bpy raises ModuleNotFoundError

# Ports are DISCOVERED from the library, not hardcoded: a wall has a port block
# per band per end, and the bands vary -- stone_brick tiles are 1/2" taller and
# carry a third (high) bay.  Anything matching `Port Block-<L>.side[.<band>].<A|B>`
# is a port.  Full-width blocks (no .A/.B) are the band as a whole and are not
# probed individually.
PORT_RE = r"^Port Block-{L}\.side(?:\.(?P<band>[A-Za-z0-9+]+))?\.(?P<end>[AB])$"
# Cutter names are not consistent between libraries -- `Side.Negative-A.Openlock.A`,
# `Side.Negative-IA.A`, `Side.Negative-BA.low.A`, `Side.Negative-Filament-BA.A` --
# so cutters are matched to port blocks by POSITION, not by name.  A cutter's
# style is read off its name; anything with no style word is openlock.
STYLE_WORDS = {"dragonbite": "dragonbite", "infinitylock": "infinitylock",
               "filament": "filament", "openlock": "openlock"}
INSET = 25.4 / 8                 # 1/8": the clip exits the side here
OUTSET = 2.0                     # how far PAST the mating plane the probe box reaches
BEHIND = 2.0                     # how far BEHIND it -- without this the probe cannot
                                 # tell "flush" from "recessed 0.5mm": with no material
                                 # past the plane the intersection is simply empty.
                                 # (NOT `INSET` -- that name is taken above, by the fill
                                 # probe, and reusing it silently changes the fill.)
# The texture must sit BEHIND the mating plane, not merely flush.  Material
# coincident with the plane alters the mesh where the port block gets unioned
# on, and coplanar faces are what a boolean handles worst -- measured with
# --face-check: +0.002mm proud fragments the block's face into 8 extra verts,
# -0.004mm leaves it untouched.  It flips exactly at zero.
#
# Gates on the PROTRUSION, not a volume.  Volume tracks the area of the smear
# rather than its depth and ranks ports wrong: one reading 0.02mm3 stood
# 0.054mm proud while one reading 0.03mm3 was only 0.042mm proud.
PROUD_MAX = -0.01                # mm; NEGATIVE is behind the plane, which is what we want
# How far --trim pulls a port face back.  Hand sculpting reliably converges on
# FLUSH (two families landed at 0.000 +/- 0.005), which is the one value that is
# neither pass nor fail and sits inside the probe's own ~0.004mm noise.  0.05 is
# 10x that noise, so a trimmed port is unambiguous.
CLEARANCE = 0.05
GREEN = 99.95                    # Devon: 100% only.  A port with any material
                                 # missing behind it does not get cut.


def volume(obj):
    """World-space volume.  calc_volume() reads mesh data, which does not carry
    the object's scale -- without the transform a scaled box measures as a unit
    cube and every port reads 100%."""
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    bm = bmesh.new()
    bm.from_mesh(ev.data)
    bm.transform(ev.matrix_world)
    v = bm.calc_volume()
    bm.free()
    return abs(v)


def world_bounds(obj):
    cs = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return ([min(c[i] for c in cs) for i in range(3)],
            [max(c[i] for c in cs) for i in range(3)])


def boolean(obj, other, op):
    m = obj.modifiers.new("b", "BOOLEAN")
    m.operation, m.object, m.solver = op, other, "MANIFOLD"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=m.name)


def trim_port(piece, block, clearance):
    """Cut the port's band back to `clearance` BEHIND the mating plane.

    Runs after Devon's sculpt, never instead of it: a sculpt also deals with
    texture flowing into the port from above its face, which a cut at the plane
    cannot see.  This only guarantees the last few hundredths, which is the part
    a hand pass cannot hit reliably.

    The box is the block's y/z footprint plus a small margin.  Spanning the
    footprint *exactly* leaves material surviving right on the boundary, and the
    probe -- which measures that same footprint -- then finds it: ports came back
    at -0.021 and -0.025 instead of the -0.050 asked for, with every worst point
    sitting on a band edge.  Overshooting the cut by a margin puts the probe's
    band strictly inside the trimmed region, so the result is exactly the
    clearance requested and does not depend on edge geometry.
    """
    MARGIN = 0.3
    lo, hi = world_bounds(block)
    on_minus = abs(lo[0]) > abs(hi[0])
    plane = lo[0] if on_minus else hi[0]
    if on_minus:
        x0, x1 = plane - OUTSET, plane + clearance
    else:
        x0, x1 = plane - clearance, plane + OUTSET
    bpy.ops.mesh.primitive_cube_add(size=1)
    box = bpy.context.object
    box.scale = [x1 - x0, hi[1] - lo[1] + 2 * MARGIN, hi[2] - lo[2] + 2 * MARGIN]
    box.location = [(x0 + x1) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2]
    bpy.context.view_layer.update()
    boolean(piece, box, "DIFFERENCE")
    bpy.data.objects.remove(box, do_unlink=True)


def centre(obj):
    lo, hi = world_bounds(obj)
    return [(lo[i] + hi[i]) / 2 for i in range(3)]


def style_of(name):
    low = name.lower()
    for word, style in STYLE_WORDS.items():
        if word in low:
            return style
    return "openlock"          # unqualified names are the openlock cutters


def match_cutters(blocks, cutters, style):
    """Pair each port block with the nearest cutter of the requested style whose
    bounding box overlaps it."""
    out = {}
    for port, blk in blocks.items():
        blo, bhi = world_bounds(blk)
        bc = centre(blk)
        best, bestd = None, None
        for cut in cutters:
            if style_of(cut.name) != style:
                continue
            clo, chi = world_bounds(cut)
            if any(chi[i] < blo[i] or clo[i] > bhi[i] for i in range(3)):
                continue                       # no overlap: different port
            cc = centre(cut)
            d = sum((cc[i] - bc[i]) ** 2 for i in range(3))
            if bestd is None or d < bestd:
                best, bestd = cut, d
        out[port] = best
    return out


def overhang(piece, block):
    """Signed distance from the port's mating plane to the nearest piece material.

    -> (max_protrusion_mm, (y, z) of the worst point).  POSITIVE sticks out past
    the plane; NEGATIVE sits behind it, which is what a clean core looks like.

    A port block ends at the face the clip mates against.  Sculpted stone
    texture can bulge past that face, which fouls the clip even though the port
    itself is full -- the fill probe cannot see it, because it only looks inside
    the block.  So look just outside the block's outer X face, within its own
    y/z band, and measure what is there.  A sculpted core reads 0.

    Volume alone badly understates this.  What fouls a clip is a few small
    pimples of stone, and a pimple 0.3mm proud has almost no volume -- 0.33mm3
    over a port looked "borderline" as a number while looking obviously wrong on
    screen.  The protrusion distance is the number that matches the eye, and the
    y/z tells Devon where to go in Blender instead of hunting the whole face.
    """
    lo, hi = world_bounds(block)
    if abs(lo[0]) > abs(hi[0]):          # this port is on the -X end
        x0, x1 = lo[0] - OUTSET, lo[0] + BEHIND
    else:
        x0, x1 = hi[0] - BEHIND, hi[0] + OUTSET
    bpy.ops.mesh.primitive_cube_add(size=1)
    box = bpy.context.object
    box.scale = [x1 - x0, hi[1] - lo[1], hi[2] - lo[2]]
    box.location = [(x0 + x1) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2]
    bpy.context.view_layer.update()
    boolean(box, piece, "INTERSECT")
    # The intersection is the piece material in a slab straddling the plane, so
    # its furthest-out vertex gives a SIGNED answer: past the plane or behind it.
    on_minus = abs(lo[0]) > abs(hi[0])
    plane = lo[0] if on_minus else hi[0]
    worst, at = None, None
    for vert in box.data.vertices:
        w = box.matrix_world @ vert.co
        d = (plane - w[0]) if on_minus else (w[0] - plane)
        if worst is None or d > worst:
            worst, at = d, (w[1], w[2])
    bpy.data.objects.remove(box, do_unlink=True)
    if worst is None:                    # no material in the band at all
        return -BEHIND, None
    assert -BEHIND - 1e-4 <= worst <= OUTSET + 1e-4, f"{worst} outside the probe box"
    return worst, at


def probe(piece, block):
    """Percentage of the port's volume that has material behind it."""
    lo, hi = world_bounds(block)
    if abs(lo[0]) > abs(hi[0]):
        lo[0] += INSET
    else:
        hi[0] -= INSET
    bpy.ops.mesh.primitive_cube_add(size=1)
    box = bpy.context.object
    box.scale = [hi[i] - lo[i] for i in range(3)]
    box.location = [(lo[i] + hi[i]) / 2 for i in range(3)]
    bpy.context.view_layer.update()
    full = volume(box)
    boolean(box, piece, "INTERSECT")
    pct = volume(box) / full * 100 if full else 0.0
    bpy.data.objects.remove(box, do_unlink=True)
    return pct


def face_verts(obj, block, eps=1e-3):
    """Vertices of `obj` lying IN the block's outer mating face."""
    lo, hi = world_bounds(block)
    plane = lo[0] if abs(lo[0]) > abs(hi[0]) else hi[0]
    n = 0
    for v in obj.data.vertices:
        w = obj.matrix_world @ v.co
        if (abs(w[0] - plane) <= eps
                and lo[1] - eps <= w[1] <= hi[1] + eps
                and lo[2] - eps <= w[2] <= hi[2] + eps):
            n += 1
    return n


def nonmanifold(obj):
    import bmesh                       # after bpy, never at module top
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--piece", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style", choices=sorted(set(STYLE_WORDS.values())),
                    default="openlock")
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--trim", action="store_true",
                    help="post-sculpt cleanup: cut each usable port's band back to "
                         "--clearance behind the mating plane and write the core to "
                         "--out.  Does NOT replace sculpting.")
    ap.add_argument("--clearance", type=float, default=CLEARANCE, metavar="MM")
    ap.add_argument("--min-fill", type=float, default=GREEN, metavar="PCT",
                    help="only trim ports at least this full; a port that will never be "
                         "cut should not get a pointless pocket.  Default is the normal "
                         "100%% rule; pass e.g. 99 for a shape like door+arched narrow.A "
                         "where the archway clips a corner the clip never touches.")
    ap.add_argument("--face-check", action="store_true",
                    help="union each port block on its own and report what the boolean "
                         "did to the block's mating face -- extra vertices in that face "
                         "and non-manifold edges.  This measures whether a flush core "
                         "actually corrupts the union, rather than assuming it does.")
    a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])

    letter = a.library.split("library.primary.")[1].split(".blend")[0]
    port_re = re.compile(PORT_RE.format(L=re.escape(letter)), re.I)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    # cutters carry no reliable prefix either (`Side.Negative-A.Openlock.A`,
    # `Negative-D.Openlock...`, `Negative-Q-Side.A`), so take every negative for
    # this letter and match by position later
    cut_pat = re.compile(rf"negative.*{re.escape(letter)}|negative-{re.escape(letter)}\b", re.I)
    with bpy.data.libraries.load(a.library, link=False) as (src, dst):
        block_names = sorted(n for n in src.objects if port_re.match(n))
        names = list(block_names)
        if not a.probe_only:
            names += [n for n in src.objects
                      if cut_pat.search(n) and not re.search(r"port ?block", n, re.I)]
        dst.objects = names
    if not block_names:
        raise SystemExit(f"no `Port Block-{letter}.side...A/B` objects in "
                         f"{os.path.basename(a.library)}")
    for o in dst.objects:
        bpy.context.scene.collection.objects.link(o)
    bpy.context.view_layer.update()
    blocks = {n[len(f"Port Block-{letter}."):]: bpy.data.objects[n] for n in block_names}
    cutters = [o for o in bpy.data.objects
               if o.type == "MESH" and o.name not in block_names]
    ports = sorted(blocks)
    print(f"# {len(ports)} ports in {os.path.basename(a.library)}")

    known = {o.name for o in bpy.data.objects}
    bpy.ops.wm.stl_import(filepath=a.piece)
    piece = next(o for o in bpy.data.objects if o.type == "MESH" and o.name not in known)
    bpy.context.view_layer.update()

    if a.trim:
        done = []
        for p in ports:
            pct = probe(piece, blocks[p])
            before, _ = overhang(piece, blocks[p])
            if pct < a.min_fill:
                print(f"# {p:16s} fill {pct:6.1f}%  {before:+.3f}mm  skipped (not usable)")
                continue
            trim_port(piece, blocks[p], a.clearance)
            after, _ = overhang(piece, blocks[p])
            print(f"# {p:16s} fill {pct:6.1f}%  {before:+.3f} -> {after:+.3f}mm vs plane")
            done.append(p)
        if not done:
            raise SystemExit("no usable ports to trim on this piece")
        print(f"# non-manifold edges after trim: {nonmanifold(piece)}")
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.wm.stl_export(filepath=a.out, export_selected_objects=True)
        print(f"# wrote {os.path.basename(a.out)}  trimmed {len(done)}: {', '.join(done)}")
        return

    if a.face_check:
        base_nm = nonmanifold(piece)
        print(f"# core itself: {len(piece.data.vertices)} verts, "
              f"{base_nm} non-manifold edges")
        for p in ports:
            proud, _at = overhang(piece, blocks[p])
            own = face_verts(blocks[p], blocks[p])
            cp = piece.copy()
            cp.data = piece.data.copy()
            bpy.context.scene.collection.objects.link(cp)
            boolean(cp, blocks[p], "UNION")
            got, nm = face_verts(cp, blocks[p]), nonmanifold(cp)
            bpy.data.objects.remove(cp, do_unlink=True)
            print(f"# {p:16s} {proud:+7.3f}mm vs plane   face verts "
                  f"{own:3d} -> {got:4d} ({got - own:+4d})   "
                  f"non-manifold edges {base_nm} -> {nm} ({nm - base_nm:+d})")
        return

    verdict = {}
    for p in ports:
        pct = probe(piece, blocks[p])
        proud, at = overhang(piece, blocks[p])
        verdict[p] = pct >= GREEN and proud <= PROUD_MAX
        blo, bhi = world_bounds(blocks[p])
        # print the band's z range: two ports at the same height means a block
        # has not been moved into place, which a percentage alone will not show
        why = "" if verdict[p] else (
            " needs sculpting" if pct >= GREEN else "")
        where = f" at y={at[0]:6.2f} z={at[1]:6.2f}" if at else ""
        print(f"# {p:16s} z=[{blo[2]:6.2f},{bhi[2]:6.2f}]  fill {pct:6.1f}%  "
              f"{proud:+7.3f}mm vs plane (need <={PROUD_MAX}){where}  "
              f"{'green' if verdict[p] else 'RED' + why}")

    green = [p for p in ports if verdict[p]]
    if not green:
        raise SystemExit("no usable ports on this piece")
    if a.probe_only:
        return

    cuts = match_cutters({p: blocks[p] for p in green}, cutters, a.style)
    unmatched = [p for p, c in cuts.items() if c is None]
    if unmatched:
        raise SystemExit(f"no {a.style} cutter overlaps {unmatched} in "
                         f"{os.path.basename(a.library)}")
    for p in green:                       # blocks first: the cuts go through them
        boolean(piece, blocks[p], "UNION")
    for p in green:
        print(f"# cut {p:12s} with {cuts[p].name!r}")
        boolean(piece, cuts[p], "DIFFERENCE")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    piece.select_set(True)
    bpy.context.view_layer.objects.active = piece
    bpy.ops.wm.stl_export(filepath=a.out, export_selected_objects=True)
    print(f"# wrote {os.path.basename(a.out)} with {len(green)}/4 ports: {', '.join(green)}")


if __name__ == "__main__":
    main()
