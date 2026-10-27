# Rendering an assembled wall-on-tile

How `shots/wall-on-tile.png` was made: the OpenForge guide's default "wall on tile" build,
with the three parts placed together and rendered as one scene. Done 2026-09-26.

Script: `bin/assemble-tile.py`. Result: `shots/wall-on-tile.png`.

## The parts

The guide engine in openforge-catalog resolved these answers:
method=wall-on-tile, size=2x2, wall-height=normal, connectors=none,
texture=dungeon_stone, floor-texture=dungeon_stone, base-clips=magnetic + OpenLOCK topless,
pegs=off.

It returned these three parts:

| role  | file | md5 |
|---|---|---|
| wall  | `dungeon_stone#wall.A.openforge.stl` | `0ddb1b2020f193f27de372857d2b7803` |
| floor | `dungeon_stone%block#floor+s2w+wall.2x2.openforge.stl` | `00354077087ede614c97ed2c51d8d798` |
| base  | `plain#base+square.2x2.openlock+topless,magnetic+flex.stl` | `08e057025fc32aa754c49798095e03ad` |

Download each one from `https://objects.openforge.tools/models/<md5[:6]>/<md5>.stl` and save
them as `wall.stl`, `floor.stl` and `base.stl`. The script reads those names from the
current directory.

For a different build, ask the openforge-catalog session to resolve another set of answers.
A different size or layout also needs new placement numbers (see below); extra parts go in
with `--add`.

## Placement

No fitting is needed. All three STLs are authored in the same tile space, on a shared 6mm
base plane. Measured with trimesh/manifold3d slices:

| part | authored bounds (mm) | what it is | placement |
|---|---|---|---|
| base  | x 0–50.8, y 0–50.8, z 0–6.01 | Open frame ("topless"): about 1250 of 2580 mm² solid in cross-section, hollow in the middle | as-is |
| floor | x 0–50.8, y 12.7–50.8, z 0–5.54 | Solid slab, 1.5" deep. The ½" strip at y<12.7 is left open for the wall | z **+6.0**, onto the frame |
| wall  | x ±25.5, y ±6.5, z 5.98–50.78 | "A" length (2"), centred on the origin, bottom already at base height | x **+25.4**, y **+6.35**, into the strip |

- **There's no socket.** The wall's `connection|openforge` means it's the openforge variant:
  the bottom 6mm is cut off so it stands on a 6mm base. It doesn't seat into anything; it
  sits on the frame next to the floor.
- **The short side of the floor faces the wall.** The floor's half-depth row of flagstones
  is along y=12.7, so it runs right along the wall. Check with `--top`.
- The assembly is rotated 180° (the `--spin` default) about the tile centre so the wall
  stands at the back, away from the camera.
- **Joining is optional.** `--join out.stl` merges all three in one manifold3d union:
  42,491 mm³ against 42,508 mm³ for the separate parts, so only about 17 mm³ overlaps at the
  seams. The result is 387k triangles. Rendering keeps them as separate objects, which
  makes moving them around free. Only join when you need a single file.

## Look

The scene comes from `shot.py`: backdrop sweep, key/fill/rim area lights, camera framing
and Standard view transform with Medium High Contrast look. The script imports those
helpers and changes three things.

**Stone material** (`stone()` in the script). All three parts use it, so the base matches
the stone.
- A low-contrast noise gives a light stone colour, `STONE = (0.46, 0.40, 0.31)`, with only
  15% darkening in the blotches.
- The grout is darkened by an **Ambient Occlusion** node, multiplied over the stone colour.
  **It doesn't need UVs**: the grout lines are real grooves in the mesh, so ambient
  occlusion finds them from the geometry. The same shading darkens the openings in the base
  a little.
- A fine noise bump and roughness 0.85 make the surface look matte and printed.

**Backdrop.** It's near-white behind the tile, fading to light grey at the edges, instead
of `shot.py`'s dark corners.

**Framing.** The camera fits the whole bounding sphere (`--fit 1.05`). The first version
used 0.8 and cut off the base.

The defaults are the settings you approved (v05 in the session):

| flag | default | effect |
|---|---|---|
| `--light` | 1.0 | stone brightness multiplier |
| `--mottle` | 0.15 | 0 is flat colour, 1 is black blotches |
| `--grout-mm` | 2.5 | how far ambient occlusion looks; roughly the grout depth |
| `--grout-lo` / `--grout-hi` | 0.5 / 0.92 | occlusion level that counts as full grout / clean stone; raise both for darker, wider grout |
| `--yaw` / `--pitch` | 28 / 24 | camera angle in degrees |
| `--drybrush` | 0 | drybrush highlight strength; 1.5 with `--light 0.55` is the tested look |
| `--db-edge` / `--db-width` / `--db-break` | 0.50 / 0.03 / 0.5 | where the highlight starts catching, how far it spreads, how patchy it is |
| `--db-min-z` | 7.6 | no drybrush below this world z (the floor slab's square bottom edge) |
| `--fit` | 1.05 | framing; below 1 crops the base |
| `--top` | off | orthographic view straight down |
| `--wall` / `--floor` / `--base` | see placement | x,y,z position of each part |
| `--parts` | base,floor,wall | which parts to load; `base,floor` for a floor-only shot |
| `--spin` | 180 | degrees to turn the assembly about the tile centre; turn the model, not `--yaw`, since the lights don't follow the camera |
| `--wood` | none | comma list of part names (e.g. `door`) painted as stained wood: grain bands and a tan drybrush highlight instead of stone |
| `--metal` | none | comma list of part names painted as silvery iron (bands, handles) |
| `--add` | none | `NAME:x,y,z[,rotz]`, repeatable: load `NAME.stl` drybrushed, turned about its own origin, then moved |
| `--samples` / `--res` | 24 / 1000 | 24 is a preview; use 256 for the final render |

## Drybrush look (optional)

`--drybrush 1.5 --light 0.55` makes the model look painted: a darker basecoat, the ambient
occlusion wash in the grout, and a pale bone highlight (`DRYBRUSH`) on the raised edges.
Test renders db01–db04 in the session; db04 is the keeper (db03 coverage with the slab band fixed).

- **Edges** come from the Geometry node's Pointiness output. Cycles computes it per vertex:
  0.5 is flat and higher is convex. `--db-edge 0.50 --db-width 0.03` sets where the ramp
  starts and how wide it is. The more conservative 0.51/0.04 (db02) catches only the
  sharpest edges.
- **Up-facing bias**: normal.z scales the highlight from 0.5 on the sides to 1 on the tops,
  the way a brush dragged across the top would catch.
- **Patchy coverage**: a scale-90 noise breaks up the highlight (`--db-break`; 0 is even
  coverage).
- **The base is left out.** It's only about 3k faces, so Pointiness smears across whole
  faces and the frame turns uniformly pale. It keeps the basecoat.
- **Nothing below world z 7.6** (`--db-min-z`). The floor's bottom ~1.6mm is a square-cut slab
  of big triangles, and the same smearing turned it into a white band. The value is base
  top (6) plus slab (1.6), specific to this build.

`--drybrush` defaults to 0, so the plain approved look is unchanged.

## Guide card images

The openforge-catalog guide cards use this script's drybrushed look: `--drybrush 1.5
--light 0.55 --samples 256 --res 640`, then WebP at quality 85. The catalog session
uploads them to R2 as `guides/guide.<key>.webp`.

| card | parts | output |
|---|---|---|
| wall | the three parts above | `shots/wall-on-tile-drybrush-640.webp` |
| floor | `dungeon_stone%block#floor.2x2.openforge.stl` (md5 `e1d767845e1862fff3a8dc04e86133c6`) as `floor.stl` and the base above, with `--parts base,floor` | `shots/floor-on-base-drybrush-640.webp` |
| corner | see below | `shots/corner-on-tile-drybrush-640.webp` |
| door | see below | `shots/door-on-tile-drybrush-640.webp` |

The corner card stages the 2x2 `openlock+topless,magnetic+flex` base (md5
`08e057025fc32aa754c49798095e03ad`, the wall card's base) and the `dungeon_stone/s2w/corner/` floor, left and
right wings and `col+L` column as `base`/`floor`/`left`/`right`/`col.stl`. `--spin 270`
puts the corner at the back left, so the default camera and lights look into it:

```
--parts base,floor --add left:31.62,-8.81,0 --add right:-23.84,31.61,0,270 \
    --add col:-8.45,6.55,0 --spin 270
```

The door card is a full 2x2 tile like the wall card. It uses the corner card's base,
`plain#base+square.2x2.openlock+topless,magnetic+flex.stl` (`base.stl`), and the s2w floor
`dungeon_stone%block#floor+s2w+wall.2x2.openforge.stl` (`floor.stl`), moved to y 0..38.1 so
its half-width row runs along the wall. The wall is
`dungeon_stone%block#wall,door+arched+standard.A.openforge.stl` (`wall.stl`), in the
38.1..50.8 strip.

The released `wood#door+standard.stl` is one fused shell, so the iron can't take its own
material. Devon's shells version,
`OpenForgeDevelopment/accessories/doors/curved/inch/rounded_door.wood_grain.standard.upright.shells.stl`,
has four shells: the wood, two bands and the handle. Shift it by (+1.35, -2.54, +31.0) into the
released door's position (fitted to within 0.16mm), then split it into `doorwood.stl` (the
largest shell) and `dooriron.stl` (the other three).

The door is raised 0.5mm for a little clearance under it. At 0.75mm its rounded top starts to
cut into the arch.

```
--parts base,floor --floor 0,-12.7,6 --add wall:25.4,44.45,0 \
    --add doorwood:25.4,44.45,0.5 --add dooriron:25.4,44.45,0.5 \
    --wood doorwood --metal dooriron --spin 0
```

## Run it

`bin/ab` mounts the current directory as `/work`, plus `/home` and `/mnt`. Run from the
directory holding the STLs; the script path must be under `/home` or `/mnt`.

```
mkdir -p ~/wot && cd ~/wot
for m in 0ddb1b2020f193f27de372857d2b7803:wall 00354077087ede614c97ed2c51d8d798:floor \
         08e057025fc32aa754c49798095e03ad:base; do
  h=${m%%:*}; curl -sfo ${m#*:}.stl https://objects.openforge.tools/models/${h:0:6}/$h.stl
done
AB_ENTRYPOINT=python ~/MasterworkTools/autoblender/bin/ab \
    ~/MasterworkTools/autoblender/bin/assemble-tile.py wall-on-tile.png --samples 256
```

## Traps hit on the way

- Don't name a scratch script `inspect.py`. It shadows the stdlib module, and numpy's
  import then fails with a circular-import error from trimesh.
- The image has no `shapely` or `rtree`, so trimesh's `section().polygons_full` and ray
  casting don't work. Use `Manifold.slice(z).area()` for cross-section probes.
- Don't compare bpy node wrappers with `is`; it's unreliable, and it deleted the Material
  Output node. Compare `bl_idname` like `shot.py` does.
- Blender's `render.filepath` needs an absolute path inside the container; a bare `out.png`
  fails with "cannot save".
