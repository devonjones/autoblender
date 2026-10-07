#!/usr/bin/env python3
"""QC every STL under a texture's updates/ folder, per file.

    AB_ENTRYPOINT=python bin/ab bin/qc-updates.py <updates-dir-or-file> [--fix]

Prints watertight / bodies / non-manifold edges / z range / volume, and each
file's volume against its `openlock` sibling.  Do NOT read a fixed expectation
into that percentage: support volume scales with wall length, so the delta is
letter-dependent -- measured 2026-09-23, 21.8mm3 on IA (1x, 0.21% of the piece),
43.6mm3 on BA (1.5x, 0.26%), 153.5mm3 on Q (4x, 0.54%).  A short piece can even
come out LARGER than its shipped sibling (door+rectangular narrow.IA reads
+0.07%: the core is +0.29% and the supports only -0.21%).  To judge a suspicious
number, build the SUPPORTED version from the same core and compare that to
shipped -- it separates a core that differs from a node group misbehaving.

--fix drops zero-volume bodies (<= FIN_MM3) and rewrites the file.  These are
float32 slivers: manifold3d's output is clean, but writing STL at float32 folds
a few near-coincident triangles into fins that hang off the real body on shared
edges.  They carry no volume, but they break watertightness and the next
boolean.  A file is only rewritten if dropping them leaves it watertight.
"""
import argparse
import os
import re

import numpy as np
import trimesh

FIN_MM3 = 0.01   # fins are ~0; real details go down to ~0.9mm3 (environmental_controls)


def stats(m):
    e = np.bincount(trimesh.grouping.unique_rows(m.edges_sorted)[1])
    return m.is_watertight, len(m.split(only_watertight=False)), int((e > 2).sum())


def sibling_key(fn):
    """Split a filename into the parts needed to find its openlock sibling.

    -> (stem, non-openlock option tags, the full option string).  Three values,
    not the two an earlier version of this docstring claimed:
    'x#wall.A.openlock+unsupported,pegs.stl' -> ('x#wall.A', 'pegs',
    'openlock+unsupported,pegs')
    """
    if "." not in fn[:-4]:
        # no option component: not a generated variant, so it has no sibling.
        # Raising here aborted the whole report AFTER every file had loaded and
        # after --fix had already rewritten some of them.
        return fn[:-4], "", ""
    stem, opt = fn[:-4].rsplit(".", 1)
    rest = [t for t in opt.split(",") if t.split("+")[0] != "openlock"]
    return stem, ",".join(rest), opt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args()

    found = [a.root] if os.path.isfile(a.root) else []
    for d, _, fs in os.walk(a.root):
        found += [os.path.join(d, f) for f in fs if f.endswith(".stl")]
    vols, rows = {}, []
    for f in sorted(found):
        m = trimesh.load(f, process=True)
        wt, bodies, nm = stats(m)
        note = ""
        if a.fix and not wt:
            keep = [p for p in m.split(only_watertight=False) if abs(p.volume) > FIN_MM3]
            c = trimesh.util.concatenate(keep)
            c.merge_vertices()
            if c.is_watertight:
                c.export(f)
                m = trimesh.load(f, process=True)
                wt, bodies, nm = stats(m)
                note = " (fins dropped)"
            else:
                note = " (fix FAILED)"
        fn = os.path.basename(f)
        vols[fn] = m.volume
        rows.append((fn, wt, bodies, nm, m.bounds[0][2], m.bounds[1][2], m.volume, note))

    for fn, wt, bodies, nm, z0, z1, v, note in rows:
        stem, rest, opt = sibling_key(fn)
        ref = f"{stem}.{','.join(filter(None, ['openlock', rest]))}.stl"
        vs = ""
        if opt.split(",")[0].startswith("openlock+") and ref in vols:
            vs = f"  vs {re.sub(r'^.*#', '', ref[:-4])} {100 * (v / vols[ref] - 1):+.2f}%"
        flag = "" if (wt and bodies == 1 and nm == 0) else "  <-- CHECK"
        print(f"{fn:70s} wt={wt!s:5s} bodies={bodies} nm={nm:<3d} "
              f"z=[{z0:.2f},{z1:.2f}] vol={v:9.1f}{vs}{note}{flag}")


if __name__ == "__main__":
    main()
