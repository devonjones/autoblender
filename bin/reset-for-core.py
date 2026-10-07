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

    hits = []
    for r in body:
        if "side" not in r[i["missing_option"]]:
            continue                      # only side locks touch the port region
        if a.all_sides and r[i["texture"]] != a.all_sides:
            continue
        if a.texture and r[i["texture"]] != a.texture:
            continue
        if a.cores and not any(c in r[i["core_path"]] for c in a.cores):
            continue
        if not r[i["out_path"]] or not os.path.exists(r[i["out_path"]]):
            continue
        hits.append(r)

    for r in hits:
        print(f"   {os.path.basename(r[i['out_path']])}")
        if not a.dry:
            r[i["status"]] = ""

    if a.dry:
        print(f"dry run: {len(hits)} would be reset")
        return
    # Clear the statuses FIRST, then delete.  The old order unlinked the files
    # before persisting, so a failure mid-loop left status="" rows pointing at
    # files that no longer existed -- and apply-gaps skips any row whose output
    # is missing only after checking status, so those rows were stranded.
    og.write_tsv(TSV, hdr, body)
    for r in hits:
        out = r[i["out_path"]]
        if os.path.exists(out):
            os.unlink(out)
    print(f"reset {len(hits)} rows -- run apply-gaps to rebuild")


if __name__ == "__main__":
    main()
