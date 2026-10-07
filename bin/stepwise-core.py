#!/usr/bin/env python3
"""Union a shells file one shell at a time, and say which shells misbehave.

    AB_ENTRYPOINT=python bin/ab bin/stepwise-core.py in.shells.stl out.core.stl

`ab core` unions every shell in one batch, which is fast but cannot tell you
WHICH shell broke the result.  This goes largest-first, round-trips every
intermediate through float32 STL (that is where fins and pinched edges appear),
and holds back any shell that leaves the result non-manifold.  Held shells are
appended to the output un-unioned, so nothing is lost; Devon fixes them in the
source and re-exports.

Also reports shells that create voids, shells that are almost entirely inside
the wall, and bodies left floating (with their gap to the main body) -- the
usual cause is a detail that touches the wall face-to-face, or sits a micron
off it, instead of overlapping.
"""
import io
import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from autoblender.union import MIN_TRIS, MIN_VOL, repair_shell, shells, to_trimesh  # noqa: E402

FIN_MM3 = 0.01  # same floor as qc-updates.py; real parts go down to ~0.9mm3


def roundtrip(man):
    """(non-manifold edges, open edges, bodies) after a float32 STL round trip."""
    buf = io.BytesIO()
    to_trimesh(man).export(buf, file_type="stl")
    buf.seek(0)
    r = trimesh.load(buf, file_type="stl", process=True)
    # Zero-volume fins are float32 noise that qc-updates.py --fix drops; holding a
    # shell for them held the BASE on 3x.D.  Judge what is left after dropping them.
    r = trimesh.util.concatenate([p for p in r.split(only_watertight=False)
                                  if abs(p.volume) > FIN_MM3])
    r.merge_vertices()
    c = np.bincount(trimesh.grouping.unique_rows(r.edges_sorted)[1])
    return int((c > 2).sum()), int((c == 1).sum()), len(r.split(only_watertight=False))


def voids(m):
    return sum(1 for c in m.decompose() if c.volume() < 0)


def pair_open_halves(tm, groups):
    """Join open shells that close each other; returns [(original index, faces)].

    A body with an internal sheet glued along a seam splits into open halves
    (shells() only joins across 2-face edges).  Fan-filling each half makes two
    bogus solids, so pair them first.  Leftover open shells (the sheets) fall
    through to repair_shell; their fan fill is ~0.02mm3, above FIN_MM3, and the
    internal-shell check in main() is what drops them.  Seen on every nova_trail
    column export.  The original index is kept so every report line agrees.
    """
    # ponytail: O(n^2) over open shells and pairs only two at a time -- a shell
    # that absorbs one partner can't take a third; grow groups transitively if a
    # body ever splits into 3+ open pieces.
    def closed(idx):
        s = tm.submesh([idx], append=True, repair=False)
        s.merge_vertices()
        return s.is_watertight and s.volume > MIN_VOL
    open_ = [k for k, fi in enumerate(groups) if not closed(fi)]
    paired, absorbed = set(), set()
    for a in open_:
        for b in open_:
            if a < b and a not in paired and b not in paired \
                    and closed(np.concatenate([groups[a], groups[b]])):
                print(f"shells {a}+{b}: open halves that close each other -- joined")
                groups[a] = np.concatenate([groups[a], groups[b]])
                paired |= {a, b}
                absorbed.add(b)
    return [(k, fi) for k, fi in enumerate(groups) if k not in absorbed]


def repair_all(tm, groups):
    """[(index, manifold or None if unrepairable, submesh)], zero-volume shells dropped."""
    parts = []
    for i, fi in groups:
        sub = tm.submesh([fi], append=True, repair=False)
        man, rung = repair_shell(sub)
        if man is None:
            print(f"shell {i}: UNREPAIRABLE ({len(fi)} tris) -- held")
            parts.append((i, None, sub))
        elif abs(man.volume()) >= MIN_VOL:
            if rung != "ok":
                print(f"shell {i}: repaired ({rung})")
            parts.append((i, man, sub))
    return parts


def main(src, dst):
    tm = trimesh.load(src, force="mesh")
    groups = pair_open_halves(tm, [fi for fi in shells(tm.faces) if len(fi) >= MIN_TRIS])

    parts = repair_all(tm, groups)
    good = sorted((p for p in parts if p[1] is not None), key=lambda p: -p[1].volume())
    held = [p for p in parts if p[1] is None]
    print(f"{len(parts)} shells")

    u = good[0][1]
    for i, man, sub in good[1:]:
        b = sub.bounds
        tag = (f"shell {i:2d} {len(sub.faces):5d}f {man.volume():9.2f}mm3 "
               f"x[{b[0][0]:.1f},{b[1][0]:.1f}] y[{b[0][1]:.1f},{b[1][1]:.1f}] "
               f"z[{b[0][2]:.1f},{b[1][2]:.1f}]")
        c = u + man
        nm, op, _ = roundtrip(c)
        if nm or op:
            print(f"{tag}  HELD ({nm} non-manifold, {op} open after union)")
            held.append((i, man, sub))
            continue
        if voids(c) > voids(u):
            print(f"{tag}  creates a void")
        added = c.volume() - u.volume()
        if added < 0.01 * man.volume():
            print(f"{tag}  internal (adds {added:.3f}mm3)")
        u = c

    nm, op, bodies = roundtrip(u)
    comps = sorted(u.decompose(), key=lambda c: -c.volume())
    print(f"UNION vol={u.volume():.3f} bodies={bodies} voids={voids(u)} nm={nm} open={op} "
          f"held={[h[0] for h in held]}")
    for c in comps[1:]:
        if c.volume() > 0:
            bb = c.bounding_box()
            print(f"  floating body {c.volume():.3f}mm3 x[{bb[0]:.1f},{bb[3]:.1f}] "
                  f"z[{bb[2]:.1f},{bb[5]:.1f}] gap {comps[0].min_gap(c, 2.0):.4f}mm")
    # fins only exist once float32 positions are merged, so drop them from a reload
    buf = io.BytesIO()
    to_trimesh(u).export(buf, file_type="stl")
    buf.seek(0)
    out = trimesh.load(buf, file_type="stl", process=True)
    out = trimesh.util.concatenate([p for p in out.split(only_watertight=False)
                                    if abs(p.volume) > FIN_MM3])
    out.merge_vertices()
    trimesh.util.concatenate([out] + [h[2] for h in held]).export(dst)
    r = trimesh.load(dst, process=True)
    c = np.bincount(trimesh.grouping.unique_rows(r.edges_sorted)[1])
    print(f"wrote {os.path.basename(dst)}: {len(r.faces)} tris wt={r.is_watertight} "
          f"nm={int((c > 2).sum())} bodies={len(r.split(only_watertight=False))} "
          f"x[{r.bounds[0][0]:.3f},{r.bounds[1][0]:.3f}] z[{r.bounds[0][2]:.3f},{r.bounds[1][2]:.3f}]")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
