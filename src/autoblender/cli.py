import argparse
import os
import traceback
import sys

from . import out
from .io import input_stl_path, output_stl_path, reject_same_path


class _LazyOps:
    """Defer `import bpy` until a command actually touches it -- it costs ~7s,
    and `ab union` never needs Blender at all."""

    def __getattr__(self, name):
        from . import ops
        return getattr(ops, name)


ops = _LazyOps()


def cmd_info(args):
    with input_stl_path(args.input) as path:
        ops.reset_scene()
        obj = ops.import_stl(path)
        nm = ops.manifold_vert_count(obj)
        print(f"name      {obj.name}", file=out)
        print(f"vertices  {len(obj.data.vertices)}", file=out)
        print(f"polygons  {len(obj.data.polygons)}", file=out)
        print(f"non_manifold_verts  {nm}", file=out)


def cmd_decimate(args):
    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        ops.reset_scene()
        obj = ops.import_stl(ip)
        ops.apply_decimate(obj, args.ratio)
        ops.export_stl(obj, op)


def cmd_remesh(args):
    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        ops.reset_scene()
        obj = ops.import_stl(ip)
        ops.apply_remesh(
            obj,
            voxel_size=args.voxel_size,
            mode=args.mode,
            adaptivity=args.adaptivity,
        )
        ops.export_stl(obj, op)


def cmd_simplify(args):
    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        ops.reset_scene()
        obj = ops.import_stl(ip)
        ops.apply_remesh(
            obj,
            voxel_size=args.voxel_size,
            mode=args.mode,
            adaptivity=args.adaptivity,
        )
        ops.apply_decimate(obj, args.ratio)
        ops.export_stl(obj, op)


def cmd_core(args):
    from . import union as _union  # deferred: pulls trimesh/manifold3d, not needed elsewhere

    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        r = _union.union_stl(
            ip, op,
            min_frac=args.min_solid_frac,
            drop_voids=args.drop_voids,
            tol=args.simplify,
        )
    print(
        f"shells {r['shells']}  {r['tris_in']} -> {r['tris']} tris  "
        f"solids={r['solids']}  voids={r['voids']}  volume={r['volume']:.3f}"
        + (f"  LOST {r['lost']:.4f} mm3" if abs(r["lost"]) > 1e-6 else ""),
        file=sys.stderr,
    )
    rungs = " ".join(f"{k}={v}" for k, v in sorted(r["rungs"].items()) if k != "ok")
    if rungs:
        print(f"  repaired: {rungs}", file=sys.stderr)
    if r["dropped"] or r["specks"]:
        print(f"  dropped: {r['dropped']} noise shells, {r['specks']} specks", file=sys.stderr)
    if r["failed"]:
        print(f"  UNREPAIRABLE shells (index, tris): {r['failed']}", file=sys.stderr)


def cmd_cut(args):
    from . import cut as _cut  # deferred: trimesh/manifold3d only, no bpy

    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        r = _cut.cut_stl(ip, op, args.z, args.keep)
    if r["failed"]:
        print(f"  UNREPAIRABLE shells (index, tris): {r['failed']}", file=sys.stderr)


def cmd_shot(args):
    from . import shot as _shot  # deferred: heavy scene build, not needed elsewhere

    r = _shot.shot(args.input, args.output, samples=args.samples, res=args.res)
    print(f"shot {args.output}  {r['tris']} faces  "
          f"emblem: field={r['emblem_field_faces']} rim={r['emblem_rim_faces']}  "
          f"plinth={r['plinth']}  post={r['post']}  "
          f"{r['res']}px @ {r['samples']}spp", file=sys.stderr)


def cmd_cat(args):
    ops.open_blend(args.blend)
    import bpy
    for o in bpy.data.objects:
        print(o.name, file=out)


def cmd_gn_apply(args):
    reject_same_path(args.input, args.output)
    overrides = {}
    for s in args.set:
        if "=" not in s:
            raise RuntimeError(f"--set expects NAME=VALUE, got {s!r}")
        k, v = s.split("=", 1)
        overrides[k] = v
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        ops.reset_scene()
        obj = ops.import_stl(ip)
        ops.apply_geometry_nodes(obj, args.node_group, args.blend, overrides or None,
                                 solver=args.solver, nudge_z=args.nudge_z)
        ops.export_stl(obj, op)


def cmd_gn_list(args):
    ops.open_blend(args.blend)
    groups = ops.list_geometry_node_groups()
    if not groups:
        print("(no geometry node groups)", file=out)
    for g in groups:
        print(f"\n{g['name']}", file=out)
        for s in g["inputs"]:
            d = f"  = {s['default']!r}" if "default" in s else ""
            items = f"  {{{', '.join(s['items'])}}}" if s.get("items") else ""
            print(f"  in   {s['type']:24s} {s['name']}{d}{items}", file=out)
        for s in g["outputs"]:
            print(f"  out  {s['type']:24s} {s['name']}", file=out)
    users = ops.list_geometry_node_modifier_users()
    if users:
        print("\nobjects with gn modifiers:", file=out)
        for obj_name, mod_name, ng_name in users:
            print(f"  {obj_name}.{mod_name} -> {ng_name}", file=out)


def cmd_render(args):
    if args.mode == "substitute":
        ops.render_substitute_walls(
            args.blend,
            args.output,
            args.stl,
            camera_name=args.camera,
            hide_non_targets=not args.keep_existing,
        )
    elif args.mode == "passthrough":
        ops.render_passthrough(
            args.blend,
            args.output,
            camera_name=args.camera,
            hide_patterns=args.hide_pattern,
            show_only_patterns=args.show_only_pattern,
            camera_from_blend=args.camera_from_blend,
        )
    else:
        ops.render_with_stls(
            args.blend,
            args.output,
            args.stl,
            gap=args.gap,
            axis=args.axis,
            camera_name=args.camera,
            hide_existing=not args.keep_existing,
            sort=args.sort,
            center=not args.no_center,
        )


def cmd_gn_check(args):
    per_file = []
    for path in args.blends:
        ops.open_blend(path)
        groups = ops.list_geometry_node_groups()
        match = next((g for g in groups if g["name"] == args.node_group), None)
        per_file.append(match)

    labels = _short_labels([os.path.basename(b) for b in args.blends])

    rows = []
    seen = set()
    types_seen = {}
    for fdata in per_file:
        if fdata is None:
            continue
        for direction, sockets in (("in", fdata["inputs"]), ("out", fdata["outputs"])):
            for s in sockets:
                key = (direction, s["name"])
                if key not in seen:
                    seen.add(key)
                    rows.append(key)
                types_seen.setdefault(key, set()).add(s["type"])

    missing_files = [l for l, f in zip(labels, per_file) if f is None]
    if missing_files:
        print(
            f"# node group {args.node_group!r} absent from: {', '.join(missing_files)}",
            file=out,
        )

    if not rows:
        print(f"# no instances of node group {args.node_group!r} found", file=out)
        return

    cells = []
    for direction, name in rows:
        cells.append([_socket_cell(fdata, direction, name) for fdata in per_file])

    col_widths = [
        max(len(labels[i]), max((cells[r][i].__len__() for r in range(len(rows))), default=1))
        for i in range(len(labels))
    ]
    row_labels = [_fmt_row_label(d, n, types_seen[(d, n)]) for d, n in rows]
    row_label_w = max((len(rl) for rl in row_labels), default=10)

    print(f"\n{args.node_group} across {len(args.blends)} files:", file=out)
    print(
        f"{'':<{row_label_w}}  "
        + "  ".join(f"{l:<{w}}" for l, w in zip(labels, col_widths)),
        file=out,
    )
    for rl, row_cells in zip(row_labels, cells):
        print(
            f"{rl:<{row_label_w}}  "
            + "  ".join(f"{c:<{w}}" for c, w in zip(row_cells, col_widths)),
            file=out,
        )

    mismatches = []
    for (direction, name), row_cells in zip(rows, cells):
        type_mismatch = len(types_seen[(direction, name)]) > 1
        value_mismatch = len(set(row_cells)) > 1
        if type_mismatch or value_mismatch:
            mismatches.append((direction, name, type_mismatch))

    if mismatches:
        print("\nMISMATCHES:", file=out)
        for direction, name, type_mismatch in mismatches:
            note = "  (type differs)" if type_mismatch else ""
            print(f"  {direction} {name}{note}", file=out)
    else:
        print("\nall sockets uniform across files", file=out)


def _short_labels(names):
    if len(names) <= 1:
        return [n.removesuffix(".blend") or n for n in names]
    pfx = os.path.commonprefix(names)
    rev = [n[::-1] for n in names]
    sfx = os.path.commonprefix(rev)[::-1]
    if sfx and not sfx.startswith("."):
        sfx = ""
    out_labels = []
    for n in names:
        s = n[len(pfx):]
        if sfx:
            s = s[: len(s) - len(sfx)]
        out_labels.append(s or n.removesuffix(".blend") or n)
    return out_labels


def _fmt_row_label(direction, name, types):
    if len(types) == 1:
        short = next(iter(types)).replace("NodeSocket", "")
        return f"{direction}  {name} ({short})"
    return f"{direction}  {name} (?)"


def _socket_cell(fdata, direction, name):
    if fdata is None:
        return "-"
    sockets = fdata["inputs"] if direction == "in" else fdata["outputs"]
    for s in sockets:
        if s["name"] == name:
            if "default" in s:
                return repr(s["default"])
            return "+"
    return "-"


def _parse_vec(text):
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 3:
        raise RuntimeError(f"--move expects X,Y,Z, got {text!r}")
    return parts


def cmd_bool(args):
    """Combine an STL with named helper objects from a .blend, in the order
    given on the command line."""
    reject_same_path(args.input, args.output)
    with input_stl_path(args.input) as ip, output_stl_path(args.output) as op:
        ops.reset_scene()
        obj = ops.import_stl(ip)

        cache = {}

        def helper(name):
            if name not in cache:
                cache[name] = ops.append_object(args.blend, name)
            return cache[name]

        if args.align:
            ops.align_min(obj, helper(args.align), args.align_axes)
        if args.move:
            ops.translate(obj, _parse_vec(args.move))

        for spec in args.clamp:
            name, _, rest = spec.partition(":")
            axes, _, gate = rest.partition(":")
            n = ops.clamp_to(obj, helper(name), axes or "X", gate or None)
            print(f"clamp {name}: {n} verts pulled in", file=sys.stderr)

        for spec in args.op:
            kind, _, name = spec.partition(":")
            if not name:
                raise RuntimeError(f"--op expects KIND:NAME, got {spec!r}")
            tool = helper(name)
            if kind == "union":
                ops.boolean(obj, tool, "UNION", args.solver)
            elif kind == "diff":
                ops.boolean(obj, tool, "DIFFERENCE", args.solver)
            elif kind == "join":
                ops.join(obj, tool)
                tool = None
            else:
                raise RuntimeError(f"unknown --op kind {kind!r}")
            cache.pop(name, None)
            if tool is not None:
                ops.drop(tool)

        if args.remesh:
            ops.apply_remesh(obj, voxel_size=args.remesh)
        if args.decimate:
            ops.apply_decimate(obj, args.decimate)
        ops.export_stl(obj, op)


def main():
    p = argparse.ArgumentParser(prog="ab")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("info", help="Print stats about an STL")
    sp.add_argument("input", nargs="?", default="-", help="Input STL or '-' for stdin")
    sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("decimate", help="Apply a decimate modifier")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--ratio", type=float, required=True)
    sp.set_defaults(fn=cmd_decimate)

    sp = sub.add_parser("remesh", help="Apply a remesh modifier")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--voxel-size", type=float, default=0.1)
    sp.add_argument("--adaptivity", type=float, default=0.0)
    sp.add_argument("--mode", default="VOXEL", choices=["VOXEL", "BLOCKS", "SMOOTH", "SHARP"])
    sp.set_defaults(fn=cmd_remesh)

    sp = sub.add_parser("simplify", help="Remesh then decimate")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--ratio", type=float, default=0.1)
    sp.add_argument("--voxel-size", type=float, default=0.1)
    sp.add_argument("--adaptivity", type=float, default=0.0)
    sp.add_argument("--mode", default="VOXEL", choices=["VOXEL", "BLOCKS", "SMOOTH", "SHARP"])
    sp.set_defaults(fn=cmd_simplify)

    sp = sub.add_parser("core", help="Union a multi-shell STL into one manifold solid core")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--simplify", type=float, default=0.0, metavar="MM",
                    help="Lossy: collapse detail within this tolerance (default 0 = off)")
    sp.add_argument("--drop-voids", action="store_true",
                    help="Lossy: discard internal cavities")
    sp.add_argument("--min-solid-frac", type=float, default=0.0, metavar="F",
                    help="Lossy: drop disconnected bodies below this fraction of the largest")
    sp.set_defaults(fn=cmd_core)

    sp = sub.add_parser("cut", help="Slice at a horizontal plane and keep one side (dropped to z=0)")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--z", type=float, required=True, help="Cut height in mm")
    sp.add_argument("--keep", default="above", choices=["above", "below"])
    sp.set_defaults(fn=cmd_cut)

    sp = sub.add_parser("shot", help="Studio product render of a tile STL")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.add_argument("--samples", type=int, default=24)
    sp.add_argument("--res", type=int, default=1000)
    sp.set_defaults(fn=cmd_shot)

    sp = sub.add_parser("cat", help="List objects in a .blend")
    sp.add_argument("blend")
    sp.set_defaults(fn=cmd_cat)

    sp = sub.add_parser(
        "render",
        help="Open a render .blend, import STLs, lay them out, and render to an image",
    )
    sp.add_argument("blend")
    sp.add_argument("output")
    sp.add_argument("stl", nargs="*")
    sp.add_argument("--mode", default="substitute", choices=["substitute", "layout", "passthrough"], help="substitute: swap mesh data of existing walls.* objects. layout: arrange imported STLs along an axis. passthrough: render the blend as-authored (with optional name filters).")
    sp.add_argument("--hide-pattern", action="append", default=[], help="(passthrough) Hide any mesh whose name contains this substring; repeatable.")
    sp.add_argument("--show-only-pattern", action="append", default=[], help="(passthrough) Hide all meshes except those whose name contains one of these substrings; repeatable.")
    sp.add_argument("--camera-from-blend", help="<blend>:<camera_name> — import a camera from another blend file and use it for the render")
    sp.add_argument("--gap", type=float, default=0.0, help="Gap between objects along the layout axis (layout mode)")
    sp.add_argument("--axis", default="X", choices=list("XYZxyz"))
    sp.add_argument("--camera", default="Camera")
    sp.add_argument("--keep-existing", action="store_true", help="Don't hide existing mesh objects in the scene")
    sp.add_argument("--sort", default="width-desc", choices=["width-desc", "width-asc", "name"])
    sp.add_argument("--no-center", action="store_true", help="Don't center the layout around origin")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("bool", help="Boolean/join an STL against helper objects in a .blend")
    sp.add_argument("blend")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument("--align", metavar="NAME",
                    help="Shift input so its bbox min matches NAME's")
    sp.add_argument("--align-axes", default="XY")
    sp.add_argument("--move", metavar="X,Y,Z", help="Translate input (after --align)")
    sp.add_argument("--clamp", action="append", default=[],
                    metavar="NAME[:AXES[:GATE]]",
                    help="Pull verts back inside NAME's bbox on AXES (default X), "
                         "for verts already inside it on GATE axes ('-' = no gate); repeatable")
    sp.add_argument("--op", action="append", default=[], metavar="KIND:NAME",
                    help="union:NAME, diff:NAME or join:NAME; applied in order")
    sp.add_argument("--solver", default="MANIFOLD", choices=["EXACT", "FLOAT", "MANIFOLD"],
                    help="Boolean solver for union/diff ops (default MANIFOLD)")
    sp.add_argument("--remesh", type=float, metavar="VOXEL")
    sp.add_argument("--decimate", type=float, metavar="RATIO")
    sp.set_defaults(fn=cmd_bool)

    gn = sub.add_parser("gn", help="Geometry nodes operations")
    gn_sub = gn.add_subparsers(dest="gn_cmd", required=True)
    sp = gn_sub.add_parser("list", help="List geometry node groups in a .blend")
    sp.add_argument("blend")
    sp.set_defaults(fn=cmd_gn_list)

    sp = gn_sub.add_parser(
        "check",
        help="Compare a node group's sockets across multiple .blend files",
    )
    sp.add_argument("node_group")
    sp.add_argument("blends", nargs="+")
    sp.set_defaults(fn=cmd_gn_check)

    sp = gn_sub.add_parser("apply", help="Apply a geometry node group to an STL")
    sp.add_argument("blend")
    sp.add_argument("node_group")
    sp.add_argument("input", nargs="?", default="-")
    sp.add_argument("output", nargs="?", default="-")
    sp.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Override an input socket value; repeatable",
    )
    sp.add_argument(
        "--nudge-z", type=float, default=0.0, metavar="MM",
        help="Shift the piece in z before applying the group. Use a small "
             "negative value (-0.02) so the OpenForge cut plane bites into solid "
             "stone instead of grazing the texture and leaving thin flanges.",
    )
    sp.add_argument(
        "--solver",
        choices=["EXACT", "FLOAT", "MANIFOLD"],
        help="Override the solver on every Mesh Boolean in the group "
             "(libraries ship EXACT, which fails on some cores)",
    )
    sp.set_defaults(fn=cmd_gn_apply)

    args = p.parse_args()
    try:
        args.fn(args)
    except Exception as e:
        # traceback first, summary last: batch drivers report the final lines
        traceback.print_exc()
        print(f"ab: error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
