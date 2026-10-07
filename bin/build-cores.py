#!/usr/bin/env python3
"""Build every missing core for a family, from its shipped openforge tiles.

Reads docs/option-gaps.tsv for the variants a family still needs, finds each
one's plain `.openforge.stl` in the release tree, and runs make-core.py with the
right letter library.  Cores land in <dev>/<texture>/peripherals/<family>/.

    bin/build-cores.py dungeon_stone portcullis [--texture-name "Dungeon Stone"] [--dry]

Skips variants that already have a core and reports the ones with no tile to
build from -- those need a real core from Blender.
"""
import argparse
import csv
import os
import subprocess
import sys

import abrun

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
RELEASE = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForge/tiles"
DEV = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForgeDevelopment/tiles"
LIB = f"{DEV}/generic/separate_wall/library"


def family_matches(row_family, want):
    """Does a queue row's family column match the --family argument?

    A bare name matches the last segment; a path matches as a suffix.  Note the
    caller MUST also filter on texture: `family` is `<texture>/<sub...>`, so a
    suffix like "/primary_walls/boss_door" matches every texture that has one.
    """
    if "/" in want:
        return row_family.endswith("/" + want)
    return row_family.split("/")[-1] == want


def tile_key(variant):
    """(key to look the openforge tile up under, whether to flip it).

    A '+reverse' variant ships no openforge tile of its own -- it only appears
    under openlock/, where the locks are already cut, so that file is not a core
    source.  It does not need one: the tile is asymmetric along its length, and
    reverse is the same part offered the other way round, so it builds from the
    plain sibling's tile turned end-for-end.
    """
    flip = "+reverse" in variant
    return (variant.replace("+reverse", "") if flip else variant), flip


def demo():
    """`bin/build-cores.py --demo`."""
    assert family_matches("dungeon_stone/separate_wall/primary_walls/boss_door",
                          "separate_wall/primary_walls/boss_door")
    assert family_matches("dungeon_stone/separate_wall/primary_walls/boss_door",
                          "boss_door")
    assert not family_matches("dungeon_stone/separate_wall/curved_walls/boss_door",
                              "separate_wall/primary_walls/boss_door")
    # the trap: a suffix match alone spans textures, so the caller filters on
    # texture too.  This marked 16 cut-stone rows reviewed once.
    assert family_matches("cut-stone/separate_wall/primary_walls/boss_door",
                          "separate_wall/primary_walls/boss_door")

    assert tile_key("t%block#wall,door+sliding+slot.2x.A") == \
        ("t%block#wall,door+sliding+slot.2x.A", False)
    assert tile_key("t%block#wall,boss_door+dungeon_door+reverse.Q") == \
        ("t%block#wall,boss_door+dungeon_door.Q", True)
    # +reverse anywhere in the name, not only at the end
    assert tile_key("t%block#wall,boss_door+dungeon_door+skull+reverse.Q") == \
        ("t%block#wall,boss_door+dungeon_door+skull.Q", True)
    print("build-cores demo ok")


def build(cmd, out):
    """Run make-core.py.  None on success, else why; a partial core is removed,
    since the next run would skip it as already built."""
    p = abrun.run(cmd, python=True, cwd=REPO)
    # make-core.py's own prints land on stderr (autoblender redirects stdout).
    # The alignment check is surfaced even on success: an unsquare tile is the
    # one defect that has got a whole batch rejected.
    for line in (p.stdout + p.stderr).splitlines():
        if line.startswith(("# wrote", "# NOT squaring")) or (
                line.startswith("# base-vs-wall") and "CHECK" in line):
            print(f"     {line}", flush=True)
    if p.returncode == 0 and os.path.exists(out):
        return None
    if os.path.exists(out):
        os.unlink(out)
    return abrun.why(p)


def main():
    if "--demo" in sys.argv:
        return demo()
    ap = argparse.ArgumentParser()
    ap.add_argument("texture")
    ap.add_argument("family", help="family path or its last segment; a bare name like "
                                   "'portcullis' matches every family with that name "
                                   "(primary_walls, curved_walls, s_system, ...), so pass "
                                   "'separate_wall/primary_walls/portcullis' to narrow it")
    ap.add_argument("--texture-name", help='base object texture, e.g. "Dungeon Stone"')
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    # "dungeon_stone" -> "Dungeon Stone" unless told otherwise
    tex_name = a.texture_name or a.texture.replace("_", " ").replace("-", " ").title()

    with open(os.path.join(REPO, "docs", "option-gaps.tsv"), newline="") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t")
                if r["texture"] == a.texture and not r["status"]
                and family_matches(r["family"], a.family)]
    if not rows:
        raise SystemExit(f"no outstanding rows for {a.texture}/{a.family}")

    # a variant's tile lives under ITS OWN family, not the first row's -- a bare
    # family name can match several families in different parts of the tree
    need = sorted({(r["family"], r["variant"]) for r in rows if r["has_core"] == "no"})
    if not need:
        print(f"all {len({r['variant'] for r in rows})} variants already have cores")
        return
    fams = sorted({f for f, _v in need})
    print(f"families matched: {', '.join(fams)}")

    tiles = {}
    for fam in fams:
        for dp, _d, fs in os.walk(os.path.join(RELEASE, fam)):
            for f in fs:
                if f.endswith(".openforge.stl"):
                    tiles.setdefault(fam, {})[f[: -len(".openforge.stl")]] = os.path.join(dp, f)

    # peripherals/<family-name>, flat -- matches peripherals/drain, /arrow_slit
    out_dir = os.path.join(DEV, a.texture, "peripherals", a.family.split("/")[-1])
    ok = missing = skipped = 0
    for fam, v in need:
        key, flip = tile_key(v)
        tile = tiles.get(fam, {}).get(key)
        if not tile:
            print(f"NO TILE  {v}", file=sys.stderr)
            missing += 1
            continue
        letter = v.rsplit(".", 1)[-1]
        blend = f"{LIB}/library.primary.{letter}.blend"
        if not os.path.isfile(blend):
            # see the note in apply-gaps.py: corner/s2w variants end in a size,
            # not a wall letter, and need the corner process instead
            print(f"NO LIBRARY for letter {letter!r} ({v}) -- corner/s2w pieces "
                  f"need the corner process, not a letter library", file=sys.stderr)
            missing += 1
            continue
        out = os.path.join(out_dir, f"{v}.core.stl")
        if os.path.exists(out):
            skipped += 1
            continue
        cmd = ["bin/make-core.py", "--library", blend,
               "--texture", tex_name, "--tile", tile, "--out", out]
        if flip:
            cmd.append("--flip")
        if a.dry:
            print(f"{letter:3s} {os.path.basename(tile)}"
                  + (f"  --flip -> {v}" if flip else ""))
            continue
        reason = build(cmd, out)
        if reason is None:
            ok += 1
        else:
            print(f"FAIL {v}: {reason}", file=sys.stderr, flush=True)
            missing += 1
    if not a.dry:
        print(f"done: {ok} cores built, {missing} failed/no tile, {skipped} already existed")
        if ok:
            # the queue still says has_core=no until the survey re-runs, and
            # apply-gaps then silently builds nothing.  Refresh it here rather
            # than leaving a stale queue for the next step to trip over.
            try:
                rc = subprocess.run([sys.executable,
                                     os.path.join(REPO, "bin", "option-gaps.py")],
                                    cwd=REPO, timeout=abrun.TIMEOUT).returncode
            except subprocess.TimeoutExpired:
                rc = "timed out"
            # Do not claim a refresh that did not happen: apply-gaps silently
            # builds nothing when the queue still says has_core=no.
            print("queue refreshed" if rc == 0 else
                  f"QUEUE REFRESH FAILED ({rc}) -- run bin/option-gaps.py before apply-gaps",
                  file=sys.stdout if rc == 0 else sys.stderr)


if __name__ == "__main__":
    main()
