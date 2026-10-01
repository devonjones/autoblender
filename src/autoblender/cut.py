"""Slice an STL with a horizontal plane and keep one side (`ab cut`).

The mesh goes through union.py's shell repair first, so the plane cut runs on a
manifold solid and manifold3d caps the cut face exactly. No bpy needed.
"""

import trimesh

from .union import union_mesh, to_trimesh


def cut_mesh(tm, z, keep="above", drop=True):
    """Return (trimesh part on `keep` side of z, union report). drop: move the part down to z=0."""
    man, report = union_mesh(tm)
    if keep == "above":
        part = man.trim_by_plane((0, 0, 1), z)
    else:
        part = man.trim_by_plane((0, 0, -1), -z)
    if part.num_tri() == 0:
        raise RuntimeError(f"nothing left {keep} z={z}")
    out = to_trimesh(part)
    if drop:
        out.apply_translation([0, 0, -out.bounds[0][2]])
    return out, report


def cut_stl(in_path, out_path, z, keep="above", drop=True):
    tm = trimesh.load(in_path, force="mesh")
    out, report = cut_mesh(tm, z, keep, drop)
    out.export(out_path)
    return report


def _selfcheck():
    box = trimesh.creation.box(extents=[10, 10, 10])   # centered: z in [-5, 5]
    top, _ = cut_mesh(box, 2.0, "above")
    assert abs(top.volume - 300) < 1e-6, top.volume
    assert abs(top.bounds[0][2]) < 1e-6 and abs(top.bounds[1][2] - 3) < 1e-6, top.bounds
    bottom, _ = cut_mesh(box, 2.0, "below")
    assert abs(bottom.volume - 700) < 1e-6, bottom.volume
    assert abs(bottom.bounds[1][2] - 7) < 1e-6, bottom.bounds
    kept, _ = cut_mesh(box, 2.0, "above", drop=False)
    assert abs(kept.bounds[0][2] - 2) < 1e-6, kept.bounds
    try:
        cut_mesh(box, 50.0)
        raise AssertionError("expected empty cut to raise")
    except RuntimeError:
        pass
    print("cut selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
