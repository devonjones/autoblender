#!/usr/bin/env python3
"""Seed queue rows for a texture that has never been released.

option-gaps.py infers gaps from the release tree, so a texture with nothing
released (nova_trail) produces no rows at all.  This writes the full option set
for every `<texture>#<piece>.<size>.<L>.core.stl` in a dev folder, using
dungeon_stone's `wall` family as the model -- its folder layout and option list.
Rows get gap_class=seeded, which option-gaps.py carries over on regeneration.

    bin/seed-gaps.py nova_trail .../nova_trail/basics/walls [--dry]
    bin/seed-gaps.py rough_stone .../rough_stone/peripherals/window+square --piece-dir window+square

Existing rows (same texture/family/variant/option) are left alone, statuses
included, so re-running only adds what is new.  Corner cores are skipped: they
take Corner.Left/Right and one end only, see docs/connection-options.md.
"""
import argparse
import csv
import importlib.util
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("og", f"{REPO}/bin/option-gaps.py")
og = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(og)

# option -> folder chain, as dungeon_stone/separate_wall/primary_walls/wall ships
# it (release + updates/).  Pegs only exist for A, IA and Q, and only go on the
# plain `wall` piece -- never a feature wall (Devon, 2026-09-23).
CHAINS = {
    "openforge":                       ("openforge",),
    "openforge,side":                  ("openforge", "side"),
    "openforge,side+dragonlock":       ("openforge", "dragonlock,side"),
    "openforge,pegs":                  ("openforge", "pegs"),
    "openforge,pegs,side":             ("openforge", "side,pegs"),
    "openlock":                        ("openlock",),
    "openlock,side":                   ("openlock", "side"),
    "openlock,pegs":                   ("openlock", "pegs"),
    "openlock,pegs,side":              ("openlock", "side,pegs"),
    "openlock+unsupported":            ("openlock+unsupported",),
    "openlock+unsupported,side":       ("openlock+unsupported", "side"),
    "openlock+unsupported,pegs":       ("openlock+unsupported", "pegs"),
    "openlock+unsupported,pegs,side":  ("openlock+unsupported", "side,pegs"),
}
PEG_LETTERS = {"A", "IA", "Q"}
CORE_RE = re.compile(r"^(?P<tex>[^#]+)#(?P<piece>[^.]+)(?:\.(?P<size>[\d.]+x))?\.(?P<L>[A-Z]+)\.core\.stl$")


def rows_for(texture, folder, piece_dir=None):
    for fn in sorted(os.listdir(folder)):
        m = CORE_RE.match(fn)
        if not m or m["tex"] != texture:
            if fn.endswith(".core.stl"):
                print(f"skipped {fn}: not a {texture} separate-wall core", file=sys.stderr)
            continue                      # corners, backups, shells: not ours
        piece, letter = m["piece"], m["L"]
        pdir = piece_dir or piece
        family = f"{texture}/separate_wall/primary_walls/{pdir}"
        variant = f"{texture}#{piece}.{letter}"
        for raw, chain in CHAINS.items():
            if "pegs" in raw and (letter not in PEG_LETTERS or piece != "wall"):
                continue
            option = og.canon(raw)
            group, args = og.mapping_for(option, family, variant)
            out = "/".join((og.DEV, texture, "updates", "separate_wall", "primary_walls",
                            pdir, *chain, f"{variant}.{og.spell(option)}.stl"))
            row = {"texture": texture, "family": family, "variant": variant,
                   "missing_option": option, "gap_class": "seeded",
                   "likely_deliberate": "no", "has_core": "yes",
                   "core_path": os.path.join(folder, fn), "node_group": group,
                   "set_args": args, "out_path": out, "status": ""}
            assert set(row) == set(og.COLUMNS), "row keys drifted from og.COLUMNS"
            yield [row[c] for c in og.COLUMNS]


def demo():
    assert CORE_RE.match("nova_trail#wall+bypass.2x.A.core.stl")["piece"] == "wall+bypass"
    assert CORE_RE.match("nova_trail#wall,bypass.2x.A.core.stl")["piece"] == "wall,bypass"
    assert CORE_RE.match("nova_trail#wall.1.5x.BA.core.stl")["L"] == "BA"
    assert not CORE_RE.match("nova_trail#wall.1.5x.BA.corner.core.stl")
    m = CORE_RE.match("rough_stone#wall,window+square.BA.core.stl")      # released names carry no size
    assert m["piece"] == "wall,window+square" and m["L"] == "BA" and m["size"] is None
    assert not CORE_RE.match("nova_trail#wall.1x.IA.core.pre-clean.stl")
    assert og.canon("openlock+unsupported,side,pegs") in {og.canon(k) for k in CHAINS}
    print("demo ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("texture")
    ap.add_argument("folder", help="dev folder holding the texture's *.core.stl")
    ap.add_argument("--piece-dir", help="family folder when it isn't the piece name "
                    "(rough_stone#wall,window+square ships in window+square/)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.piece_dir and "/" in a.piece_dir:
        ap.error("--piece-dir is one folder name, not a path")

    with open(og.OUT, newline="") as fh:
        rd = csv.reader(fh, delimiter="\t")
        next(rd)                                  # header, rewritten from og.COLUMNS
        existing = list(rd)
    have = {tuple(r[:4]) for r in existing}
    rows = list(rows_for(a.texture, a.folder, a.piece_dir))
    if not rows:
        sys.exit(f"no {a.texture}#<piece>.<L>.core.stl in {a.folder}")
    new = [r for r in rows if tuple(r[:4]) not in have]
    for r in new:
        print(f"{r[8]:22s} {r[10].split('/updates/')[-1]}")
    if not a.dry and new:
        # rewrite atomically rather than appending: a partial append leaves a
        # torn final row in the one file whose `status` column is unrecoverable
        og.write_tsv(og.OUT, og.COLUMNS, existing + new)
    print(f"{'would add' if a.dry else 'added'} {len(new)} rows")


if __name__ == "__main__":
    if "--demo" in os.sys.argv:
        demo()
    else:
        main()
