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
import collections
import csv
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
RELEASE = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForge/tiles"
DEV = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForgeDevelopment/tiles"
LIB = f"{DEV}/generic/separate_wall/library"


def main():
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
                and (r["family"].endswith("/" + a.family)
                     if "/" in a.family else r["family"].split("/")[-1] == a.family)]
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
        # A '+reverse' variant ships no openforge tile of its own -- it only
        # appears under openlock/, where the locks are already cut, so it is not
        # a core source.  It does not need one: the tile is asymmetric along its
        # length, and reverse is that same part offered the other way round, so
        # it builds from the plain sibling's tile turned end-for-end.
        flip = "+reverse" in v
        tile = tiles.get(fam, {}).get(v.replace("+reverse", "") if flip else v)
        if not tile:
            print(f"NO TILE  {v}", file=sys.stderr)
            missing += 1
            continue
        letter = v.rsplit(".", 1)[-1]
        blend = f"{LIB}/library.primary.{letter}.blend"
        if not os.path.isfile(blend):
            print(f"NO LIBRARY for letter {letter!r} ({v})", file=sys.stderr)
            missing += 1
            continue
        out = os.path.join(out_dir, f"{v}.core.stl")
        if os.path.exists(out):
            skipped += 1
            continue
        cmd = [f"{REPO}/bin/ab", "bin/make-core.py", "--library", blend,
               "--texture", tex_name, "--tile", tile, "--out", out]
        if flip:
            cmd.append("--flip")
        if a.dry:
            print(f"{letter:3s} {os.path.basename(tile)}"
                  + (f"  --flip -> {v}" if flip else ""))
            continue
        env = dict(os.environ, AB_ENTRYPOINT="python")
        p = subprocess.run(cmd, env=env, cwd=REPO, capture_output=True, text=True)
        # make-core.py imports autoblender.union for the repair pass, and that
        # package redirects sys.stdout to stderr (it keeps binary STL pipes
        # clean), so its later prints land on stderr.  Search both.
        out_lines = (p.stdout + p.stderr).splitlines()
        # surface the alignment check too, not just "# wrote" -- an unsquare tile
        # is the one defect that has actually got a whole batch rejected
        note = [l for l in out_lines if l.startswith("# wrote")]
        for l in out_lines:
            if l.startswith("# base-vs-wall") and "CHECK" in l:
                print(f"     {l}", flush=True)
        if p.returncode == 0 and note:
            print(note[0], flush=True)
            ok += 1
        else:
            why = [l for l in (p.stdout + p.stderr).splitlines()
                   if l.strip() and not l.startswith(("Info:", "Timer", "Read blend", "#"))]
            print(f"FAIL {v}: {why[-1] if why else 'no output'}", file=sys.stderr, flush=True)
            missing += 1
    if not a.dry:
        print(f"done: {ok} cores built, {missing} failed/no tile, {skipped} already existed")
        if ok:
            # the queue still says has_core=no until the survey re-runs, and
            # apply-gaps then silently builds nothing.  Refresh it here rather
            # than leaving a stale queue for the next step to trip over.
            subprocess.run([sys.executable, os.path.join(REPO, "bin", "option-gaps.py")],
                           cwd=REPO)
            print("queue refreshed")


if __name__ == "__main__":
    main()
