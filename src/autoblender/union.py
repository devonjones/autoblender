"""Union a multi-shell STL into a single manifold solid.

Blender's boolean solvers are unreliable on these meshes: EXACT self-union of a
multi-shell wall leaves tens of thousands of valence-4 edges. Instead, split the
mesh into shells, repair each one individually, and hand the clean set to
manifold3d's exact kernel as a single batch boolean.

No bpy import here on purpose -- this runs without Blender.
"""

import numpy as np
import trimesh
from trimesh import repair
from manifold3d import Manifold, Mesh, OpType

MIN_VOL = 1e-3   # mm^3; a shell below this is noise, not geometry
MIN_TRIS = 4     # a closed solid needs at least a tetrahedron


def shells(faces):
    """Split faces into components joined only across edges shared by exactly 2 faces.

    Splitting on shared *vertices* is not enough: two otherwise-clean shells welded
    along a single non-manifold edge come back as one unusable component. Restricting
    the join to 2-manifold edges makes them fall apart into individually valid solids.
    """
    nf = len(faces)
    e = np.sort(
        np.stack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], 1).reshape(-1, 2),
        axis=1,
    )
    fid = np.repeat(np.arange(nf), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, fid = e[order], fid[order]
    _, start, cnt = np.unique(e, axis=0, return_index=True, return_counts=True)

    parent = np.arange(nf)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in start[cnt == 2]:
        a, b = find(fid[s]), find(fid[s + 1])
        if a != b:
            parent[max(a, b)] = min(a, b)
    roots = np.array([find(i) for i in range(nf)])
    return [np.flatnonzero(roots == r) for r in np.unique(roots)]


def to_manifold(m):
    mm = Mesh(
        vert_properties=np.asarray(m.vertices, np.float32),
        tri_verts=np.asarray(m.faces, np.uint32),
    )
    mm.merge()
    man = Manifold(mm)
    return man if str(man.status()) == "Error.NoError" else None


def _oriented(m):
    """Build a manifold, flipping it if it came out inside-out.

    A shell can be perfectly valid yet wound inward -- fix_winding makes normals
    *consistent*, not outward. An inverted shell has negative volume and subtracts
    from the union instead of adding to it.
    """
    man = to_manifold(m)
    if man is not None and man.volume() < 0:
        flipped = trimesh.Trimesh(vertices=m.vertices, faces=m.faces[:, ::-1], process=False)
        man = to_manifold(flipped) or man
    return man


def close_holes(m):
    """Fan-triangulate every simple boundary loop.

    trimesh's fill_holes only closes 3- and 4-edge holes and silently does nothing
    above that; real holes here run 6-37 edges.
    """
    f = m.faces
    d = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], 1).reshape(-1, 2)
    _, inv, cnt = np.unique(np.sort(d, axis=1), axis=0, return_inverse=True, return_counts=True)
    bd = d[cnt[inv] == 1]
    if not len(bd):
        return m
    nxt = {int(a): int(b) for a, b in bd}
    verts, newf, seen = list(m.vertices), [], set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, v = [], start
        while v is not None and v not in seen:
            seen.add(v)
            loop.append(v)
            v = nxt.get(v)
        if v != start or len(loop) < 3:      # open chain, not a closed loop
            continue
        c = len(verts)
        verts.append(np.mean([m.vertices[i] for i in loop], axis=0))
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            newf.append([b, a, c])           # existing face runs a->b, so the fill runs b->a
    if not newf:
        return m
    return trimesh.Trimesh(
        vertices=np.array(verts), faces=np.vstack([f, np.array(newf)]), process=False
    )


def repair_shell(sub):
    """Escalating repair ladder. Returns (manifold_or_None, rung_that_worked)."""
    if (man := _oriented(sub)):
        return man, "ok"
    s = sub.copy()
    s.update_faces(s.nondegenerate_faces(height=1e-8))
    s.update_faces(s.unique_faces())
    s.merge_vertices()
    s.remove_unreferenced_vertices()
    if (man := _oriented(s)):
        return man, "clean"
    repair.fix_winding(s)
    if (man := _oriented(s)):
        return man, "winding"
    s.fill_holes()
    repair.fix_winding(s)
    if (man := _oriented(s)):
        return man, "fill_holes"
    s = close_holes(s)
    repair.fix_winding(s)
    if (man := _oriented(s)):
        return man, "fan_fill"
    return None, "FAILED"


def union_mesh(tm, min_frac=0.0, drop_voids=False, tol=0.0):
    """Union every shell of `tm`. Returns (Manifold, report dict)."""
    parts, rungs, dropped, failed = [], {}, 0, []
    for i, fi in enumerate(shells(tm.faces)):
        if len(fi) < MIN_TRIS:
            dropped += 1
            continue
        sub = tm.submesh([fi], append=True, repair=False)
        man, rung = repair_shell(sub)
        if man is None:
            failed.append((i, len(fi)))
            continue
        if abs(man.volume()) < MIN_VOL:
            dropped += 1
            continue
        rungs[rung] = rungs.get(rung, 0) + 1
        parts.append(man)

    u = Manifold.batch_boolean(parts, OpType.Add)
    exact_vol = u.volume()
    # decompose() returns internal cavities too, as negative-volume components
    comps = u.decompose()
    bodies = sorted((x for x in comps if x.volume() > MIN_VOL), key=lambda x: -x.volume())
    voids = len(comps) - len(bodies)
    specks = [x for x in bodies[1:] if x.volume() < bodies[0].volume() * min_frac] if bodies else []
    keep = [x for x in bodies if x not in specks]
    if specks or drop_voids:
        # rebuilding from the positive bodies is also what discards internal voids
        u = Manifold.batch_boolean(keep, OpType.Add)
    if tol:
        u = u.simplify(tol)
    return u, dict(
        shells=len(parts), rungs=rungs, dropped=dropped, specks=len(specks),
        failed=failed, solids=len(keep), voids=voids, tris=u.num_tri(),
        volume=u.volume(), lost=exact_vol - u.volume(),
    )


def to_trimesh(man):
    m = man.to_mesh()
    return trimesh.Trimesh(
        vertices=m.vert_properties[:, :3].astype(np.float64), faces=m.tri_verts, process=False
    )


def union_stl(in_path, out_path, **kw):
    tm = trimesh.load(in_path, force="mesh")
    u, report = union_mesh(tm, **kw)
    report["tris_in"] = len(tm.faces)
    to_trimesh(u).export(out_path)
    return report


def _selfcheck():
    box = lambda: trimesh.creation.box(extents=[10, 10, 10])

    # two cubes overlapping by 5mm -> 1000 + 1000 - 500
    a, b = box(), box()
    b.apply_translation([5, 0, 0])
    u, r = union_mesh(trimesh.util.concatenate([a, b]), tol=0)
    assert r["solids"] == 1, r
    assert abs(u.volume() - 1500) < 1e-3, u.volume()

    # same, but one cube wound inside-out: the orientation guard must still add it
    a, b = box(), box()
    b.apply_translation([5, 0, 0])
    b.faces = b.faces[:, ::-1]
    u, r = union_mesh(trimesh.util.concatenate([a, b]), tol=0)
    assert r["solids"] == 1, r
    assert abs(u.volume() - 1500) < 1e-3, u.volume()

    # cylinder missing an end cap: a 16-edge hole, past what fill_holes handles
    c = trimesh.creation.cylinder(radius=5, height=10, sections=16)
    keep = np.abs(c.triangles_center[:, 2]) < 4.9
    c.update_faces(keep)
    c.remove_unreferenced_vertices()
    u, r = union_mesh(c, tol=0)
    assert r["rungs"].get("fan_fill") == 1, r["rungs"]
    assert r["solids"] == 1 and not r["failed"], r
    expect = 16 * 0.5 * 5 ** 2 * np.sin(2 * np.pi / 16) * 10   # 16-gon prism
    assert abs(u.volume() - expect) / expect < 0.02, (u.volume(), expect)

    print("union selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
