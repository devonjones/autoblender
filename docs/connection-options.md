# Connection options (the geometry-nodes pass)

How `.core.stl` files become the OpenLOCK / OpenForge / DragonLock variants that ship.

**Status: reconstructed, then verified against the files.** The two sessions that built and
ran this (2026-05-06 and 2026-05-28 → 2026-07-24) have been pruned from
`~/.claude/projects`; only their prompts survive in `~/.claude/history.jsonl`. The socket
tables below were read back out of `library.primary.IA.blend` with `ab gn list` on
2026-09-21 and confirmed identical across A/IA/BA/D/Q with `ab gn check`; the folder and
naming rules come from the produced files in the release tree.

## The mechanism

Each wall profile letter has a library `.blend` holding several geometry node groups. A
group takes the raw wall as geometry and adds the connection hardware — locks, supports,
port blocks — driven by boolean/menu sockets. `ab` applies one headless:

```
ab gn apply LIBRARY.blend NODE_GROUP in.core.stl out.stl --set Name=Value ...
```

The pipeline, end to end:

```
*.shells.stl  --ab core-->  *.core.stl  --ab gn apply-->  *.<options>.stl
```

Libraries live in
`~/Dropbox/projects/Hardware/objects/OpenForgeDevelopment/tiles/generic/separate_wall/library/`:

| letter | file |
|---|---|
| A  | `library.primary.A.blend` |
| IA | `library.primary.IA.blend` |
| BA | `library.primary.BA.blend` |
| D  | `library.primary.D.blend` |
| Q  | `library.primary.Q.blend` |
| corner | `library.primary.Corner.blend` ⚠ see below |
| columns | `library.columns.blend` |

⚠ `library.primary.Corner.blend` holds only `Base`, `Base Maker`, `Wall` and `Wall, Low`
— **no `OpenForge`, `OpenForge+Side` or `OpenLock` group**, so it is not what produced the
corner option files. Corners go through `library.primary.BA.blend`, which is the only
library carrying `Corner.Left` and `Corner.Right`.

The letter in the STL filename (`aztlan#window.2x.A.core.stl`) picks the library. Sizes
(`1x`, `1.5x`, `2x`, `3x`, `4x`) are a property of the piece, not of the library — and the
released filenames drop the size for separate-wall pieces while keeping it for s2w corners.

## Filename grammar

The authority is the catalog's own parser,
`~/MasterworkTools/openforge-catalog/openforge/data/scanner.py` (`parse_filename`,
`parse_form`, `parse_texture`, `parse_connection`). A released STL is:

```
<texture>#<form>.<size>.<connection>.stl

aztlan#window.A.openforge,side+dragonlock.stl
   |     |    |         |
   |     |    |         `-- connection: comma-joined SET of tags
   |     |    `------------ size (a wall profile letter here; 2x, 1.5x elsewhere)
   |     `----------------- form: comma-joined parts, each with +options
   `----------------------- texture, optionally <main>%<sub>, each with +options
```

### Two axes

A connection string carries at most two independent things:

| axis | what it is | values |
|---|---|---|
| **base** | how the bottom of the tile connects | `openforge` (bottom 6mm cut, separate glued-on base) or `openlock` (openlock base built in). Options: `+unsupported`, `+topless` |
| **side locks** | locks on the *side* of the wall | absent, or `side`, `side+dragonlock`, `side+filament` |

`side` on its own means openlock side locks — the catalog fills in `side+openlock` for you
(`_handle_side`). Devon leaves the `+openlock` off deliberately: `openlock,side+openlock`
reads as though openlock were being said twice, when the first one is the base and the
second the side lock. So `openlock` in a filename always names the **base**.

Every `side+dragonlock` file is on an `openforge` base, which keeps it unambiguous. That is
not only a naming convention — it is what the node groups can actually produce. The
`OpenLock` group exposes `Side Locks` as a plain boolean with no style menu, so openlock-base
side locks are always openlock-style; only `OpenForge+Side` carries a `Lock Style` menu.
There is no node group that would make an openlock-base DragonLock side lock.

Rules that matter when comparing or generating names:

- **Commas are a set, not a sequence.** `openforge,magnetic` and `magnetic,openforge` are
  the same connection. Sort before comparing — `bin/option-gaps.py:canon()` does this.
- **`+` hangs options off the thing before it**: `openlock+unsupported` is the `openlock`
  tag with the `unsupported` option, which is *not* the same shape as `openlock,side`
  (two tags). Options are likewise unordered.
- **Size can contain dots** (`1.5x1.5`), so the field is not simply "the second
  component" — the catalog re-joins any following component starting with a digit, and
  normalises `2.0x2.0` to `2x2`.
- **A bare `side` tag implies `side+openlock`** (`_handle_side`). Some column files are
  named `...col+I.side.stl` inside an `openlock/` folder, relying on this.

## The node groups

`ab gn list library.primary.IA.blend` (2026-09-21). **`OpenForge` and `OpenLock` are
separate node groups**, one per value of the **base** axis (see *Two axes* above) — that is
the fork the whole output tree hangs off.

**`OpenForge`** — the OpenForge base variant:

| socket | type | default |
|---|---|---|
| Top Buffer | Bool | True |

**`OpenForge+Side`** — OpenForge with side locks; the `side` family:

| socket | type | default |
|---|---|---|
| Lock Style | Menu | `OpenLock` |
| Lock Style (Low) | Menu | `OpenLock` |
| Low | Bool | False |
| Port Block | Bool | True |
| Top Buffer | Bool | True |

**`OpenLock`** — openlock base built into the tile, full height. Note `Side Locks` is a
plain boolean: openlock-base side locks are always openlock-style, which is why every
`side+dragonlock` file in the tree sits on an openforge base.

| socket | type | default |
|---|---|---|
| OpenLock Supports | Bool | True |
| Side Locks | Bool | False |
| Port Blocks | Bool | True |
| Low | Bool | False |

Also present, and worth knowing about now that OpenForge is understood as a base system:

```
Base
  Lock System = 'Magnetic'  {Magnetic, OpenLock, OpenLock (No Supports),
                             OpenLock (Topless), DragonLock}
```

`Base` (with `Base Maker`) is what makes the glued-on base, and it carries its own lock
system choice — including two OpenLock variants that have no equivalent in the wall-side
`Lock Style` menu. Nothing in this pipeline drives it yet. `Wall` and `Wall.Low` are
plumbing.

Note the two menus in `OpenForge+Side`: `Lock Style` is the live one at `Low=False`, and
`Lock Style (Low)` takes over when `Low=True`. That is the "a menu unlocks if sidelocks is
true and low is false, and a different menu if both are true" behaviour from 2026-05-30 —
it is two sockets, not one socket that changes meaning. Set the one matching your `Low`.

`ab gn list` enumerates a menu's items in `{braces}`. In `library.primary.IA.blend`:

```
Lock Style        = 'OpenLock'  {OpenLock, DragonLock}
Lock Style (Low)  = 'OpenLock'  {OpenLock, DragonLock}
```

So `OpenLock` → plain `side`, `DragonLock` → `side+dragonlock`. Verified identical in A,
IA, BA, D and Q. **There is no `Filament` item here** — that one belongs to the corner
groups, see *Corners* below.

`ab gn apply` takes a menu by name or index — `ops._menu_items()` walks from the group
input socket through reroutes to the `MENU_SWITCH` node to enumerate them, so
`--set 'Lock Style=DragonLock'` works without knowing the index.

Also watch the spelling drift between groups, which is exactly why `gn check` exists:
`OpenForge+Side` says **Port Block** (singular) while `OpenLock` says **Port Blocks**.

## The 6mm cut

Verified 2026-09-21 by applying the `OpenForge` group to `aztlan#window.1x.IA.core.stl`
and measuring, then checking the released file:

```
aztlan#window.1x.IA.core.stl              z=[ 0.000, 50.960]  h=50.960   input
OpenForge group applied                   z=[ 6.000, 50.960]  h=44.960
released aztlan#window.IA.openforge.stl   z=[ 6.000, 50.960]  h=44.960
released aztlan#window.IA.openlock.stl    z=[-0.000, 50.960]  h=50.960
```

Two things to take from this:

1. **The cut is inside the node group.** `OpenForge` removes the bottom 6mm as part of the
   apply; there is no separate cut step in this pipeline.
2. **The part stays at z=6.** It is not dropped to the build plate — the released files
   keep the 6mm gap, so the tile sits at its assembled height above the glued-on base.

⚠ This differs from `ab cut --z 6 --keep above`, which drops the kept part to z=0. Same
plane, different convention. Relevant to `drive.py`'s `cut-6mm` process, which uses
`ab cut` and will therefore emit a part at z=0 rather than z=6. Fine if the consumer just
wants something printable; wrong if they expect the OpenForge convention.

## Output → group + settings

Mapping inferred by lining the socket tables up against the released filenames. Verify the
first time you re-run this.

| output suffix | node group | `--set` |
|---|---|---|
| `openforge` | `OpenForge` | (defaults; `Top Buffer=False` for doors/windows/snakeholes) — cuts the bottom 6mm |
| `openforge,side` | `OpenForge+Side` | `Lock Style=OpenLock` |
| `openforge,side+dragonlock` | `OpenForge+Side` | `Lock Style=DragonLock` |
| `openlock` | `OpenLock` | (defaults) |
| `openlock+unsupported` | `OpenLock` | `OpenLock Supports=False` |
| `openlock,side` | `OpenLock` | `Side Locks=True` |
| `openlock+unsupported,side` | `OpenLock` | `OpenLock Supports=False` `Side Locks=True` |
| any corner | `Corner.Left` (BA library only) | `Port Block=False`, `Lock Style=OpenLock\|DragonLock\|Filament` |

`+low` pieces set `Low=True` and, in `OpenForge+Side`, take `Lock Style (Low)` instead of
`Lock Style`.

## What actually gets produced

Ground truth, from `OpenForge/tiles/aztlan/separate_wall/primary_walls/window/`:

```
openforge/                    aztlan#window.A.openforge.stl
openforge/side/               aztlan#window.A.openforge,side.stl
openforge/side,dragonlock/    aztlan#window.A.openforge,side+dragonlock.stl
openlock/                     aztlan#window.A.openlock.stl
openlock/side/                aztlan#window.A.openlock,side.stl
openlock+unsupported/         aztlan#window.A.openlock+unsupported.stl
openlock+unsupported/side/    aztlan#window.A.openlock+unsupported,side.stl
```

Rules the tree encodes:

- **One folder per option string**, with `,` kept verbatim in the folder name.
- **Filename** is `<set>#<piece>.<LETTER>.<options>.stl`. The options segment replaces
  `core`. Within one system, sub-options join with `,`; a variant of a sub-option joins
  with `+` (`openforge,side+dragonlock`).
- `side` folders nest *under* the system folder they modify.
- **IA is absent from every `side` folder** — sidelocks conflict with the gaps in those
  walls. For doors, only D got the side treatment.
- Everything got `aztlan` before the `#`. (Learned the hard way — a whole batch was
  renamed on 2026-05-30.)

### Corners

Corners are their own family, not a subfolder of the wall they match:

```
s2w/corner/window#corner+s2w/aztlan#corner,window.2x.openforge,side.stl
                             aztlan#corner,window.2x.openforge,side+dragonlock.stl
                             aztlan#corner,window.2x.openforge,side+filament.stl
```

Corners use their own node groups, which exist **only in `library.primary.BA.blend`**:

```
Corner.Left     Lock Style = 'OpenLock'  {OpenLock, DragonLock, Filament}
Corner.Right    Lock Style = 'OpenLock'  {OpenLock, DragonLock, Filament}
                Lock Style (Low), Low, Port Block, Top Buffer as in OpenForge+Side
```

That third item is where `openforge,side+filament` comes from — the wall-side
`OpenForge+Side` menu has only two. `Corner.Left` was used for all three files.

- Three files, one per `Lock Style` value. **No nested subdirectories.**
- **Port blocks off.**
- Corner-left geometry is used for all three.
- The 1.5x/BA corner walls are the corner piece of a 2x2 corner tile.

## Validating the libraries

The library blends were authored by hand, one per letter, so socket names and defaults
drift between them. That is what `ab gn check` exists for:

```
ab gn check NODE_GROUP library.primary.A.blend library.primary.IA.blend ...
```

It prints a socket × file matrix, `-` where a socket is missing, the default value where
it has one, and a MISMATCHES section for anything that differs in type or value. The
working loop is: run check, fix the odd blend in Blender, re-run check.

**Gotcha:** a node group edited in the UI will not appear to `ab` until the blend is saved
*and* the group is marked as an asset.

Drift seen on 2026-09-21 that `gn check` will not catch, because it compares one named
group at a time: the low-wall helper is `Wall.Low` in IA and D but `Wall, Low` in A, BA and
Q, and D has no plain `Wall` group at all. Harmless for this pass — none of these are
applied directly — but it means group *names* differ between libraries too, not just
sockets.

As of 2026-09-21, `OpenForge+Side` is uniform across all five letters:

```
OpenForge+Side across 5 files:
                             A           IA          BA          D           Q
in  Lock Style (Menu)        'OpenLock'  'OpenLock'  'OpenLock'  'OpenLock'  'OpenLock'
in  Lock Style (Low) (Menu)  'OpenLock'  'OpenLock'  'OpenLock'  'OpenLock'  'OpenLock'
in  Low (Bool)               False       False       False       False       False
in  Port Block (Bool)        True        True        True        True        True
in  Top Buffer (Bool)        True        True        True        True        True

all sockets uniform across files
```

## Building a core

A core is the wall **with a base on it and no locks** -- the thing every geometry node
group expects as input. The openforge variant is that same wall with the bottom 6mm cut
off, so a core can be assembled from a shipped openforge tile plus the lockless base:

```
core = openforge tile (lifted back onto the cut face) + Base-<letter>.<Texture>
       -> join -> remesh (voxel 0.1) -> decimate 0.1
```

`bin/make-core.py` does this. Verified 2026-09-22 on the dungeon_stone drains: the five
assembled cores matched the hand-made wall cores to within 0.021mm in height with the same
z origin and body count, and feeding the assembled `drain.A` core through `OpenLock`
reproduced the shipped part at **100.0% volume**.

Three things that will bite:

- **The tile has usually been dropped to z=0.** Devon repairs Blender exports in 3D
  Builder -- commonly needed, not occasional -- and the repair recenters the part, so the
  cut face no longer sits where the base expects it. Lift it back by the base's own
  height; do not assume 6mm, measure the base.
- **The tile can be off in Y by a multiple of 15mm** (the wall grid). Correct only whole
  grid steps -- the drains were all off by less than 0.09mm, which is mesh noise and must
  be left alone.
- **Base object names are not consistent.** Four libraries spell it `Base-<L>.Dungeon
  Stone`, D spells it `Base-D-Dungeon Stone`. Match `^Base-[A-Za-z]+[.-]<Texture>$` rather
  than formatting a name. (Q also carries a `Dragonllock` typo on one variant.)

The base objects also answer several of the unmapped option strings: `Magnetic+Flex`,
`Triplex` and `Topless` are **base objects**, not node group settings --
`Base-A.Dungeon Stone.Magnetic+Flex`, `.Triplex+Topless` and so on.

## Side ports

A wall carries a **port block** per band per end -- `Port Block-<L>.side.A/.B` (upper) and
`.side.low.A/.low.B` (lower) -- plus full-width blocks for each band as a whole. The block
is the positive that guarantees a flat mating surface; a separate **negative** per lock
style cuts the bay, and it deliberately runs past the side of the wall so the boolean has
something to bite on.

Most sets have two bands, four ports. **`building_facades` tiles are 1/2" taller** -- they
are meant for building exteriors, where the extra height reads better on the table -- and
carry a **third, high bay**: `Port Block-D.Side.High` and friends. So the port count is a
property of the set, not a constant. `bin/make-ports.py` discovers ports by matching
`Port Block-<L>.side[.<band>].<A|B>` rather than assuming, and prints what it found.

**Never resolve a cutter by name.** The spellings are not consistent between libraries:

```
A    Side.Negative-A.Openlock.A        .Openlock.Low.A
IA   Side.Negative-IA.A                -Low.A
BA   Side.Negative-BA.A                .low.A            Side.Negative-Filament-BA.A
Q    Negative-Q-Side.A                 Negative-Q-Side.low.A
D    Negative-D.Openlock...            (no "side" in the name at all)
```

`make-ports.py` matches each cutter to the port block it overlaps, and reads the style off
the name (`dragonbite`, `infinitylock`, `filament`, otherwise openlock).

### A port is only usable if there is material behind it

A wall feature in a band -- a drain cavity, an arrow slit -- leaves the bay cutting into
a void, which opens a gap. Probe before cutting: build a box from the block's bounds, inset
1/8" at the outer X end, and intersect it with the piece. A port with solid wall behind it
fills the box exactly.

```
plain wall IA      100.0 100.0 100.0 100.0   all usable
drain IA           100.0 100.0  64.8  64.9   lower pair blocked by the cavity
arrow_slit IA       72.3  72.4  88.9  88.9   all four blocked -- ships no side variant
```

A red port gets **neither block nor cut**: an unused block would leave a flat pad on the
wall for nothing. The two-port `drain.IA` built this way matched the hand-made shipped part
to 0.06% by volume.

## Applying: two traps

**`+low` pieces need `Low=True`** on `OpenLock` / `OpenForge+Side`. Without it the group
places side locks at full-wall height, standing ~17.5mm proud of a low wall. The height
still looks plausible per-file, so this only shows up against the shipped part.

**The libraries now default to `MANIFOLD`** — 243 Mesh Boolean nodes across the five
primary libraries were switched on 2026-09-22. They shipped as `EXACT`, which fails on
some geometry in two ways:

- `dungeon_stone#wall.D` with side locks came out a 162-poly sliver (h=35.0 instead of
  50.78). MANIFOLD and FLOAT both produced the correct 310k-poly wall.
- Every `OpenForge+Side` output was non-watertight; the same builds under MANIFOLD came
  out watertight, and cleaner than the hand-made files that ship (those are non-watertight
  too, and print fine, so this is an improvement rather than a bug fix).

MANIFOLD is the same exact-arithmetic kernel `union.py` uses. `ab gn apply --solver`
still overrides per run, in either direction, without editing a library.

Not every failure is the solver: `library.primary.Q.blend`'s `OpenLock` returns **empty
geometry** for `Low=True` under all three solvers, while the same core builds correctly
with the flag omitted. That one is a dead branch in the node tree.

## Open questions

1. **The `Base` groups disagree badly between libraries**, and nothing drives them yet:

   ```
   IA:  Lock System = 'Magnetic'  {Magnetic, OpenLock, OpenLock (No Supports),
                                   OpenLock (Topless), DragonLock}
   BA:  Locks       = 'Magnetic'  {Magnetic, OpenLock Magnetic, OpenLock Magnetic (Mirrored),
                                   OpenLock (No Supports) Magnetic, ... , DragonLock Magnetic,
                                   DragonLock Magnetic (Mirrored)}   (12 items)
   ```

   Different socket *name* and a different item set. If base generation ever gets
   automated, this needs reconciling first.
2. **Clips are not this.** On 2026-09-01 the question "how do I add all the clip options"
   was investigated and dead-ended: `walls.blend`, `generic/dragonlock.walls.blend` and
   `generic/infinitylock.walls.blend` contain **no geometry node groups**, so clipable
   walls come from some other process. Still unknown.
