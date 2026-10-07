import os
import re

import bpy
from mathutils import Vector

_SIZE_SUFFIX_RE = re.compile(r"(\d+(?:\.\d+)?x)\.([A-Za-z]+)\b")


def _parse_size_suffix(name):
    m = _SIZE_SUFFIX_RE.search(name)
    return (m.group(1), m.group(2)) if m else None


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_stl(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.stl_import(filepath=path)
    new = set(bpy.data.objects) - before
    if not new:
        raise RuntimeError(f"STL import produced no new object: {path}")
    return new.pop()


def export_stl(obj, path):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True)


def apply_decimate(obj, ratio=0.1, decimate_type="COLLAPSE"):
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = decimate_type
    if decimate_type == "COLLAPSE":
        mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)


def apply_remesh(obj, voxel_size=0.1, mode="VOXEL", adaptivity=0.0):
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("Remesh", "REMESH")
    mod.mode = mode
    if mode == "VOXEL":
        mod.voxel_size = voxel_size
        mod.adaptivity = adaptivity
    bpy.ops.object.modifier_apply(modifier=mod.name)


def open_blend(path):
    bpy.ops.wm.open_mainfile(filepath=path)


def list_geometry_node_groups():
    groups = []
    for ng in bpy.data.node_groups:
        if ng.bl_idname != "GeometryNodeTree":
            continue
        inputs, outputs = [], []
        for item in ng.interface.items_tree:
            if item.item_type != "SOCKET":
                continue
            info = {"name": item.name, "type": item.socket_type}
            try:
                info["default"] = item.default_value
            except AttributeError:
                pass
            if item.socket_type == "NodeSocketMenu" and item.in_out == "INPUT":
                info["items"] = [name for _, name in _menu_items(ng, item.identifier)]
            if item.in_out == "INPUT":
                inputs.append(info)
            elif item.in_out == "OUTPUT":
                outputs.append(info)
        groups.append({"name": ng.name, "inputs": inputs, "outputs": outputs})
    return groups


def _set_boolean_solver(ng, solver, seen=None):
    """Override the solver on every Mesh Boolean in a tree, nested groups included.

    Libraries ship these set to EXACT, which fails hard on some cores -- it
    returned a 162-poly sliver for dungeon_stone wall.D with side locks, where
    MANIFOLD and FLOAT both produced the correct 310k-poly wall.
    """
    seen = seen if seen is not None else set()
    if ng.name in seen:
        return 0
    seen.add(ng.name)
    n = 0
    for node in ng.nodes:
        if node.bl_idname == "GeometryNodeMeshBoolean":
            node.solver = solver
            n += 1
        elif node.type == "GROUP" and node.node_tree:
            n += _set_boolean_solver(node.node_tree, solver, seen)
    return n


def apply_geometry_nodes(obj, node_group_name, blend_path, inputs=None, solver=None,
                         nudge_z=0.0):
    ng = bpy.data.node_groups.get(node_group_name)
    if ng is None:
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            if node_group_name not in data_from.node_groups:
                raise RuntimeError(
                    f"node group {node_group_name!r} not in {blend_path}"
                )
            data_to.node_groups = [node_group_name]
        ng = bpy.data.node_groups.get(node_group_name)
        if ng is None:
            raise RuntimeError(f"failed to load node group {node_group_name!r}")

    if solver:
        n = _set_boolean_solver(ng, solver)
        print(f"# gn: boolean solver -> {solver} on {n} node(s)")

    if nudge_z:
        # The OpenForge cut plane sits at the base height.  Where the stone
        # texture is tangent to it the cut leaves paper-thin flanges instead of
        # a clean face, so drop the piece slightly and let the plane bite into
        # solid material.
        #
        # This MUST go into the mesh data, not obj.location: a geometry nodes
        # modifier evaluates in the object's local space, so moving the object
        # leaves the geometry exactly where the group's cut plane expects it and
        # the nudge does nothing at all.
        from mathutils import Matrix
        obj.data.transform(Matrix.Translation((0.0, 0.0, nudge_z)))
        obj.data.update()
        bpy.context.view_layer.update()
        print(f"# gn: nudged mesh {nudge_z:+.3f} in z before the cut")

    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("GeometryNodes", "NODES")
    mod.node_group = ng

    if inputs:
        sockets = {
            item.name: item
            for item in ng.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"
        }
        for name, raw in inputs.items():
            socket = sockets.get(name)
            if socket is None:
                raise RuntimeError(
                    f"unknown input socket {name!r} on node group {node_group_name!r}"
                )
            mod[socket.identifier] = _coerce_socket_value(raw, socket, ng)

    bpy.ops.object.modifier_apply(modifier=mod.name)


def _coerce_socket_value(value, socket, ng):
    st = socket.socket_type
    if st == "NodeSocketBool":
        v = value.lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"invalid bool for {st}: {value!r}")
    if st == "NodeSocketInt":
        return int(value)
    if st == "NodeSocketFloat":
        return float(value)
    if st == "NodeSocketMenu":
        try:
            return int(value)
        except ValueError:
            pass
        items = _menu_items(ng, socket.identifier)
        if not items:
            raise RuntimeError(
                f"could not enumerate menu items for socket {socket.name!r}"
            )
        for idx, name in items:
            if name == value:
                return idx
        raise RuntimeError(
            f"menu value {value!r} not in {[n for _, n in items]} "
            f"for socket {socket.name!r}"
        )
    return value


def _menu_items(ng, socket_identifier):
    input_node = next((n for n in ng.nodes if n.type == "GROUP_INPUT"), None)
    if input_node is None:
        return []
    sock = next(
        (s for s in input_node.outputs if s.identifier == socket_identifier),
        None,
    )
    if sock is None:
        return []
    queue = [link.to_node for link in sock.links]
    seen = set()
    while queue:
        node = queue.pop(0)
        if node.name in seen:
            continue
        seen.add(node.name)
        if node.type == "MENU_SWITCH":
            return [(i, item.name) for i, item in enumerate(node.enum_items)]
        if node.type == "REROUTE":
            queue.extend(link.to_node for link in node.outputs[0].links)
    return []


def list_geometry_node_modifier_users():
    users = []
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group:
                users.append((obj.name, mod.name, mod.node_group.name))
    return users


def render_with_stls(
    blend_path,
    output_path,
    stl_paths,
    *,
    gap=0.0,
    axis="X",
    camera_name="Camera",
    hide_existing=True,
    sort="width-desc",
    center=True,
):
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    if hide_existing:
        for o in bpy.data.objects:
            if o.type == "MESH":
                o.hide_render = True
                o.hide_viewport = True

    imported = []
    for path in stl_paths:
        before = set(bpy.data.objects)
        bpy.ops.wm.stl_import(filepath=path)
        for o in set(bpy.data.objects) - before:
            o.hide_render = False
            o.hide_viewport = False
            imported.append(o)

    bpy.context.view_layer.update()

    axis_idx = "XYZ".index(axis.upper())

    def world_extent(o):
        cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
        vals = [c[axis_idx] for c in cs]
        return min(vals), max(vals)

    if sort == "width-desc":
        imported.sort(key=lambda o: (world_extent(o)[1] - world_extent(o)[0]), reverse=True)
    elif sort == "width-asc":
        imported.sort(key=lambda o: (world_extent(o)[1] - world_extent(o)[0]))
    elif sort == "name":
        imported.sort(key=lambda o: o.name)

    cursor = 0.0
    for o in imported:
        mn, mx = world_extent(o)
        o.location[axis_idx] += (cursor - mn)
        cursor += (mx - mn) + gap
        bpy.context.view_layer.update()

    if center and imported:
        half = cursor - gap if imported else 0.0
        shift = -half / 2.0
        for o in imported:
            o.location[axis_idx] += shift
        bpy.context.view_layer.update()

    if camera_name in bpy.data.objects:
        bpy.context.scene.camera = bpy.data.objects[camera_name]

    scene = bpy.context.scene
    if scene.render.engine == "CYCLES":
        scene.cycles.device = "CPU"
    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)


def _relocate_missing_images(blend_path, search_dirs=()):
    """Compositor images with broken relative paths come in with has_data=False
    after a fresh open. Try to find them by basename in plausible directories
    (the blend's host parent, common siblings) and reload."""
    candidates = list(search_dirs)
    blend_dir = os.path.dirname(os.path.abspath(blend_path))
    candidates.extend([blend_dir, os.path.dirname(blend_dir)])
    host_pwd = os.environ.get("AB_HOST_PWD")
    if host_pwd:
        candidates.extend([host_pwd, os.path.dirname(host_pwd), os.path.dirname(os.path.dirname(host_pwd))])
    seen = set()
    dedup = [d for d in candidates if d and not (d in seen or seen.add(d))]
    for img in bpy.data.images:
        if img.has_data or not img.filepath:
            continue
        base = os.path.basename(img.filepath)
        for d in dedup:
            candidate = os.path.join(d, base)
            if os.path.exists(candidate):
                print(f"# render: relocating image {img.name!r} -> {candidate}")
                img.filepath = candidate
                img.reload()
                break
        else:
            print(f"# render: image {img.name!r} not found in {dedup}")


def _import_camera_from(blend_path, camera_name):
    pre = set(o.name for o in bpy.data.objects)
    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
        if camera_name not in data_from.objects:
            raise RuntimeError(
                f"camera {camera_name!r} not in {blend_path}"
            )
        data_to.objects = [camera_name]
    new_names = set(o.name for o in bpy.data.objects) - pre
    cam_obj = next(
        (bpy.data.objects[n] for n in new_names if bpy.data.objects[n].type == "CAMERA"),
        None,
    )
    if cam_obj is None:
        raise RuntimeError(f"failed to append camera {camera_name!r}")
    if cam_obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(cam_obj)
    return cam_obj


def _unhide_all_collections(scene):
    """Workbench/render visibility depends on:
      - LayerCollection.hide_viewport (per-view-layer eye icon)
      - Collection.hide_render (camera icon on collection)
      - Collection.hide_viewport (monitor icon on collection)
    Authors often hide parent collections in their working file; unhide them all
    before rendering so the scene matches what's authored."""
    def walk_lc(lc):
        lc.hide_viewport = False
        for c in lc.children:
            walk_lc(c)
    for vl in scene.view_layers:
        walk_lc(vl.layer_collection)
    for coll in bpy.data.collections:
        coll.hide_render = False
        coll.hide_viewport = False


def render_passthrough(
    blend_path,
    output_path,
    *,
    camera_name=None,
    hide_patterns=(),
    show_only_patterns=(),
    camera_from_blend=None,
):
    """Open the blend as-authored and render. Optionally hide any mesh whose
    name contains one of `hide_patterns`, or hide everything except meshes
    whose name contains one of `show_only_patterns`."""
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    _unhide_all_collections(bpy.context.scene)
    _relocate_missing_images(blend_path)

    if camera_from_blend:
        src_blend, _, cam_name = camera_from_blend.partition(":")
        if not cam_name:
            raise RuntimeError("--camera-from-blend needs <blend>:<camera>")
        imported = _import_camera_from(src_blend, cam_name)
        bpy.context.scene.camera = imported
        camera_name = None
        print(f"# render: imported camera {imported.name!r} from {src_blend}")

    hidden = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        name = obj.name
        if hide_patterns and any(p in name for p in hide_patterns):
            obj.hide_render = True
            obj.hide_viewport = True
            hidden += 1
        elif show_only_patterns and not any(p in name for p in show_only_patterns):
            obj.hide_render = True
            obj.hide_viewport = True
            hidden += 1
    print(f"# render: hidden={hidden}")

    scene = bpy.context.scene
    if camera_name and camera_name in bpy.data.objects:
        scene.camera = bpy.data.objects[camera_name]
    elif scene.camera is None:
        fallback = next((o for o in bpy.data.objects if o.type == "CAMERA"), None)
        if fallback is not None:
            print(f"# render: no scene camera set; falling back to {fallback.name!r}")
            scene.camera = fallback

    if scene.render.engine == "CYCLES":
        scene.cycles.device = "CPU"
    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)


def render_substitute_walls(
    blend_path,
    output_path,
    stl_paths,
    *,
    camera_name="Camera",
    target_prefix="walls.",
    exclude_prefix="walls+low.",
    hide_non_targets=True,
):
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    _unhide_all_collections(bpy.context.scene)
    _relocate_missing_images(blend_path)

    stl_by_key = {}
    for stl in stl_paths:
        fname = os.path.basename(stl)
        if "+corner" in fname:
            continue
        key = _parse_size_suffix(fname)
        if key is None:
            print(f"# render: couldn't parse size/suffix from {fname!r}, skipping")
            continue
        stl_by_key.setdefault(key, stl)

    imported_meshes = {}
    for key, path in stl_by_key.items():
        before = set(bpy.data.objects)
        bpy.ops.wm.stl_import(filepath=path)
        new = (set(bpy.data.objects) - before).pop()
        imported_meshes[key] = new.data
        bpy.data.objects.remove(new, do_unlink=True)

    matched, unmatched, hidden = 0, 0, 0
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        name = obj.name
        is_target = name.startswith(target_prefix) and not name.startswith(exclude_prefix)
        if not is_target:
            if hide_non_targets:
                obj.hide_render = True
                obj.hide_viewport = True
                hidden += 1
            continue

        key = _parse_size_suffix(name)
        target_mesh = imported_meshes.get(key)
        if target_mesh is None and key:
            for k, m in imported_meshes.items():
                if k[0] == key[0]:
                    target_mesh = m
                    break
        if target_mesh is None:
            obj.hide_render = True
            obj.hide_viewport = True
            unmatched += 1
            continue

        if obj.data.materials and not target_mesh.materials:
            target_mesh.materials.append(obj.data.materials[0])
        obj.data = target_mesh
        matched += 1

    print(f"# render: matched={matched} unmatched_walls={unmatched} hidden_other={hidden}")

    if camera_name in bpy.data.objects:
        bpy.context.scene.camera = bpy.data.objects[camera_name]

    scene = bpy.context.scene
    if scene.render.engine == "CYCLES":
        scene.cycles.device = "CPU"
    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)


def manifold_vert_count(obj):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.object.mode_set(mode="OBJECT")
    return sum(1 for v in obj.data.vertices if v.select)


def append_object(blend_path, name):
    """Append a single object from another .blend and link it to the scene."""
    with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
        if name not in src.objects:
            raise RuntimeError(f"object {name!r} not in {blend_path}")
        dst.objects = [name]
    obj = dst.objects[0]
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def world_bounds(obj):
    cs = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[i] for c in cs) for i in range(3)])
    hi = Vector([max(c[i] for c in cs) for i in range(3)])
    return lo, hi


def translate(obj, delta):
    obj.location += Vector(delta)
    bpy.context.view_layer.update()


def align_min(obj, target, axes="XY"):
    """Shift obj so its bounding-box minimum matches target's on `axes`."""
    lo, _ = world_bounds(obj)
    tlo, _ = world_bounds(target)
    delta = Vector((0.0, 0.0, 0.0))
    for a in axes.upper():
        i = "XYZ".index(a)
        delta[i] = tlo[i] - lo[i]
    translate(obj, delta)


def clamp_to(obj, target, axes="X", gate_axes=None):
    """Pull verts back inside target's bounding box on `axes`, so they don't
    poke through a block we're about to union on. Only verts already inside
    target's box on `gate_axes` (default: every axis not being clamped) move."""
    lo, hi = world_bounds(target)
    axis_idx = ["XYZ".index(a) for a in axes.upper()]
    if gate_axes is None:
        gate_idx = [i for i in range(3) if i not in axis_idx]
    elif gate_axes == "-":
        gate_idx = []
    else:
        gate_idx = ["XYZ".index(a) for a in gate_axes.upper()]

    mw = obj.matrix_world
    inv = mw.inverted()
    moved = 0
    for v in obj.data.vertices:
        w = mw @ v.co
        if any(w[i] < lo[i] or w[i] > hi[i] for i in gate_idx):
            continue
        new = w.copy()
        for i in axis_idx:
            new[i] = min(max(w[i], lo[i]), hi[i])
        if new != w:
            v.co = inv @ new
            moved += 1
    obj.data.update()
    return moved


def boolean(obj, target, operation, solver="MANIFOLD"):
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("Boolean", "BOOLEAN")
    mod.operation = operation
    mod.object = target
    mod.solver = solver
    bpy.ops.object.modifier_apply(modifier=mod.name)


def join(obj, target):
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()


def drop(obj):
    if obj.name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def _selfcheck():
    """Run inside the image: `python -m autoblender.ops`.

    Covers the two things in this module with no other check on them: the
    recursive solver override, and the invariant that `nudge_z` moves MESH DATA
    rather than the object transform.
    """
    import bpy
    from mathutils import Matrix

    def tree(name):
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT",
                                socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT",
                                socket_type="NodeSocketGeometry")
        gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        ng.links.new(gi.outputs[0], go.inputs[0])
        return ng

    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- _set_boolean_solver -------------------------------------------------
    # A diamond: the SAME nested group referenced twice.  Without the `seen`
    # guard its boolean is counted (and re-set) twice; worse, a genuine cycle
    # would recurse forever.  3 booleans, not 4.
    inner = tree("inner")
    inner.nodes.new("GeometryNodeMeshBoolean")
    outer = tree("outer")
    outer.nodes.new("GeometryNodeMeshBoolean")
    outer.nodes.new("GeometryNodeMeshBoolean")
    for _ in range(2):
        n = outer.nodes.new("GeometryNodeGroup")
        n.node_tree = inner

    assert _set_boolean_solver(outer, "MANIFOLD") == 3, "diamond counted twice"
    for ng in (inner, outer):
        for node in ng.nodes:
            if node.bl_idname == "GeometryNodeMeshBoolean":
                assert node.solver == "MANIFOLD", (ng.name, node.name, node.solver)
    # a group already visited contributes nothing -- this is the cycle guard
    assert _set_boolean_solver(outer, "EXACT", seen={outer.name}) == 0

    # --- nudge_z -------------------------------------------------------------
    # The whole point: a geometry nodes modifier evaluates in the object's LOCAL
    # space, so nudging obj.location is a silent no-op that still prints its
    # success line.  Assert the vertices moved and the transform did not.
    passthrough = tree("passthrough")
    mesh = bpy.data.meshes.new("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new("o", mesh)
    bpy.context.scene.collection.objects.link(obj)

    before_loc = tuple(obj.location)
    before_z = [v.co.z for v in obj.data.vertices]
    apply_geometry_nodes(obj, "passthrough", "/nonexistent.blend", nudge_z=-0.02)
    after_z = [v.co.z for v in obj.data.vertices]

    assert tuple(obj.location) == before_loc, "nudge_z moved the OBJECT, not the mesh"
    assert all(abs((a - b) - -0.02) < 1e-6 for a, b in zip(after_z, before_z)), \
        f"mesh data did not move by the nudge: {before_z} -> {after_z}"

    print("ops selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
