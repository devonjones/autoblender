#!/usr/bin/env python3
"""Add a `Pegs` geometry node group to a wall library .blend.

The pegs already exist in the libraries as loose mesh objects (`Pegs-A-Wall`,
`Pegs-IA-Wall`, `Pegs-Q-Wall`).  A loose object is not discoverable: `ab gn list`
cannot see it and `ab gn check` cannot validate it.  Wrapping it in a node group
puts pegs on the same documented interface as every other option.

    Geometry ->|
               |-> Mesh Boolean (UNION) -> Geometry
    Object Info|
    (Pegs-<L>-Wall, RELATIVE)

Run it inside the bpy container:

    docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v /home:/home \
        --entrypoint python autoblender:latest \
        bin/make-pegs-group.py <library.blend> [-o out.blend]

Without -o it saves in place.  Writes nothing if the library has no peg object.
"""
import argparse
import re
import sys

import bpy

PEG_RE = re.compile(r"^Pegs-.+-Wall$")
GROUP = "Pegs"


def build(ng_name, peg):
    ng = bpy.data.node_groups.new(ng_name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    oi = ng.nodes.new("GeometryNodeObjectInfo")
    oi.inputs["Object"].default_value = peg
    # RELATIVE applies the peg object's own transform.  ORIGINAL would hand back
    # the mesh in its local coordinates, i.e. the pegs unpositioned.
    oi.transform_space = "RELATIVE"

    bl = ng.nodes.new("GeometryNodeMeshBoolean")
    bl.operation = "UNION"
    # With operation=UNION the node exposes a single multi-input mesh socket;
    # there is no "Mesh 1" to link, both operands go into the same socket.
    mesh_in = next(i for i in bl.inputs if i.type == "GEOMETRY" and i.is_multi_input)
    ng.links.new(gi.outputs[0], mesh_in)
    ng.links.new(oi.outputs["Geometry"], mesh_in)
    ng.links.new(bl.outputs["Mesh"], go.inputs[0])

    for n, x in ((gi, -400), (oi, -400), (bl, -150), (go, 50)):
        n.location = (x, 100 if n is gi else -100 if n is oi else 0)

    # A node group with no user is dropped on save, and the libraries are
    # authored as assets -- do both, or it silently will not be there.
    ng.use_fake_user = True
    ng.asset_mark()
    return ng


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blend")
    ap.add_argument("-o", "--out", help="write here instead of in place")
    args = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv
                         else sys.argv[1:])

    bpy.ops.wm.open_mainfile(filepath=args.blend)
    pegs = [o for o in bpy.data.objects if PEG_RE.match(o.name)]
    if not pegs:
        print(f"{args.blend}: no Pegs-*-Wall object, nothing to do")
        return
    if len(pegs) > 1:
        raise SystemExit(f"{args.blend}: expected one peg object, found "
                         f"{[o.name for o in pegs]}")

    existing = bpy.data.node_groups.get(GROUP)
    if existing is not None:
        bpy.data.node_groups.remove(existing)
        print(f"replaced the existing {GROUP!r} group")

    build(GROUP, pegs[0])
    out = args.out or args.blend
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print(f"{out}: added {GROUP!r} using {pegs[0].name!r}")


if __name__ == "__main__":
    main()
