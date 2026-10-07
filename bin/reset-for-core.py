#!/usr/bin/env python3
"""Reset the side-lock rows that were built from a given core.

When a core is re-sculpted -- typically to pull stone texture back behind the
port mating plane -- every side-lock file generated from it is stale.  Files
without side locks are unaffected: they never cut into the port region.

Finds rows by the `core_path` column rather than by guessing which families used
a core, deletes their output, and clears their status so apply-gaps rebuilds
them.

    bin/reset-for-core.py '#wall.2x.A' '#drain.A'        # substrings of the core filename
    bin/reset-for-core.py --all-sides dungeon_stone      # every side row for a texture
    bin/reset-for-core.py --dry ...
"""
import argparse
import csv
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(REPO, "docs", "option-gaps.tsv")
_spec = importlib.util.spec_from_file_location("og", f"{REPO}/bin/option-gaps.py")
og = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(og)


def selected(r, a):
    """A built side-lock row matching the command-line filters."""
    return ("side" in r["missing_option"]       # only side locks touch the port region
            and (not a.all_sides or r["texture"] == a.all_sides)
            and (not a.texture or r["texture"] == a.texture)
            and (not a.cores or any(c in r["core_path"] for c in a.cores))
            and bool(r["out_path"]) and os.path.exists(r["out_path"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cores", nargs="*", help="substrings matched against core_path")
    ap.add_argument("--texture", help="restrict to one texture -- core names are NOT "
                                      "unique across textures (rough_stone#wall.2x.A "
                                      "and dungeon_stone#wall.2x.A both contain "
                                      "'#wall.2x.A'), so pass this unless you really "
                                      "mean every set")
    ap.add_argument("--all-sides", metavar="TEXTURE",
                    help="every side row of this texture, whatever its core")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if not a.cores and not a.all_sides:
        ap.error("give at least one core substring, or --all-sides TEXTURE")

    with open(TSV, newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    hdr, body = rows[0], rows[1:]
    i = {k: n for n, k in enumerate(hdr)}

    hits = [r for r in body if selected(dict(zip(hdr, r)), a)]

    for r in hits:
        print(f"   {os.path.basename(r[i['out_path']])}")
        if not a.dry:
            r[i["status"]] = ""

    if a.dry:
        print(f"dry run: {len(hits)} would be reset")
        return
    # Clear the statuses first, then delete: a failure part-way must not leave
    # rows still marked done whose files are gone.
    og.write_tsv(TSV, hdr, body)
    for r in hits:
        out = r[i["out_path"]]
        if os.path.exists(out):
            os.unlink(out)
    print(f"reset {len(hits)} rows -- run apply-gaps to rebuild")


if __name__ == "__main__":
    main()
