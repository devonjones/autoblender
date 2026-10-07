#!/usr/bin/env python3
"""Work rows out of docs/option-gaps.tsv.

Applies the row's node group to its core and writes the result to the row's
out_path -- always under a texture's updates/ folder, never the production tree.

    bin/apply-gaps.py dungeon_stone              # do the work
    bin/apply-gaps.py dungeon_stone --dry        # print what it would run
    bin/apply-gaps.py dungeon_stone --option unsupported

Skips rows with no node group, no core, a non-empty status, or a
likely_deliberate flag, plus any row whose output already exists -- so it is
resumable: run it again and it picks up where it stopped.
"""
import argparse
import csv
import os
import subprocess
import sys

# The OpenForge cut plane sits at the base height, and where the stone texture
# is tangent to it the cut leaves gross paper-thin flanges instead of a clean
# face.  Dropping the mesh 0.02 first makes the plane bite into solid material.
# Devon passed the recut portcullis Q pieces by sight on 2026-09-22.
#
# Only the OpenForge groups get it: an OpenLock group makes no bottom cut, so
# nudging there would just shorten the piece by 0.02 for nothing.
NUDGE_Z = -0.02
# Every `bin/ab` call here goes through Docker.  A stale docker.socket is a known
# failure on this box and makes the run hang forever with no output, mid-way
# through a batch of dozens of meshes.  Generous, because a Q-sized MANIFOLD
# boolean legitimately takes minutes -- this is a hang guard, not a deadline.
TIMEOUT = 1800

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(REPO, "docs", "option-gaps.tsv")
LIB = os.path.expanduser("~/Dropbox/projects/Hardware/objects/OpenForgeDevelopment"
                         "/tiles/generic/separate_wall/library")


def wanted(row, texture, option, exact=False):
    if option is not None and exact:
        return (row["texture"] == texture
                and row["has_core"] == "yes"
                and row["likely_deliberate"] == "no"
                and row["node_group"]
                and not row["status"]
                and row["missing_option"] == option)
    return (row["texture"] == texture
            and row["has_core"] == "yes"
            and row["likely_deliberate"] == "no"
            and row["node_group"]
            and not row["status"]
            and (option is None or option in row["missing_option"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("texture")
    ap.add_argument("--option", help="only rows whose option contains this")
    ap.add_argument("--exact", action="store_true",
                    help="--option must EQUAL the row's option, not just be contained in "
                         "it.  'openlock+unsupported' otherwise also picks up "
                         "'openlock+unsupported,side', which is the difference between "
                         "the rows that need no ports and the ones waiting on a sculpt.")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--nudge-z", type=float, default=NUDGE_Z, metavar="MM",
                    help="override the OpenForge cut nudge.  Pass 0 when the cores were "
                         "already dropped by hand -- Devon drops the wall 0.02 into the "
                         "base when the union overlap is poor, which moves the wall "
                         "relative to the cut plane exactly as this nudge does, so "
                         "applying both stacks to 0.04.")
    args = ap.parse_args()

    with open(TSV, newline="") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t")
                if wanted(r, args.texture, args.option, args.exact)]

    ok = fail = skip = 0
    for row in rows:
        out = row["out_path"]
        if os.path.exists(out):
            skip += 1
            continue
        letter = row["variant"].rsplit(".", 1)[-1]
        blend = f"{LIB}/library.primary.{letter}.blend"
        if not os.path.isfile(blend):
            # The letter is the last dot component of the variant, which only
            # works for real wall letters.  Corner and s2w pieces end in a size
            # instead (`.2x`, `.2x2`, `.IL+corner,90`), so they land here -- they
            # need Corner.Left/Right out of the BA library and a one-end process
            # that this script does not implement.  See docs: corners are out of
            # scope, not merely unbuilt.
            print(f"no library for letter {letter!r} "
                  f"({row['variant']} {row['missing_option']}) -- skipped; "
                  f"corner/s2w pieces need the corner process, not a letter library",
                  file=sys.stderr)
            skip += 1
            continue

        # node_group may be a ';'-separated chain (e.g. "OpenLock;Pegs"): each
        # stage feeds the next, and the --set overrides apply to the first only.
        stages = row["node_group"].split(";")
        srcs = [row["core_path"]] + [f"{out[:-4]}.stage{n}.stl" for n in range(1, len(stages))]
        cmds = []
        for n, group in enumerate(stages):
            cmd = [f"{REPO}/bin/ab", "gn", "apply", blend, group, srcs[n],
                   srcs[n + 1] if n + 1 < len(srcs) else out]
            if n == 0:
                for kv in filter(None, row["set_args"].split(";")):
                    cmd += ["--set", kv]
                if group.startswith("OpenForge") and args.nudge_z:
                    cmd += ["--nudge-z", str(args.nudge_z)]
            cmds.append(cmd)

        if args.dry:
            print(f"{row['node_group']:16s} {os.path.basename(blend):26s} "
                  f"{out.split('/updates/')[-1]}  {row['set_args']}"
                  + (f"  [nudge {args.nudge_z}]"
                     if stages[0].startswith("OpenForge") and args.nudge_z else ""))
            continue

        os.makedirs(os.path.dirname(out), exist_ok=True)
        for n, cmd in enumerate(cmds):
            try:
                proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True,
                                      timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                print(f"TIMEOUT {os.path.basename(out)} after {TIMEOUT}s "
                      f"({row['node_group']}) -- check for a stale docker.socket",
                      file=sys.stderr, flush=True)
                proc = subprocess.CompletedProcess(cmd, 1, stderr="timed out")
                break
            if proc.returncode != 0:
                break
            if n + 1 < len(cmds):
                # A float32 fin on an intermediate makes it non-manifold, and the
                # next stage's MANIFOLD boolean then returns empty geometry
                # ("does not contain a mesh") -- seen on OpenLock;Pegs.
                #
                # Keep its output and check it: qc-updates.py signals a failed
                # repair only by printing "(fix FAILED)" -- it has no non-zero
                # exit -- so discarding both streams was the one place that
                # could ever have surfaced it.
                try:
                    fix = subprocess.run([f"{REPO}/bin/ab", f"{REPO}/bin/qc-updates.py",
                                          srcs[n + 1], "--fix"],
                                         env={**os.environ, "AB_ENTRYPOINT": "python"},
                                         capture_output=True, text=True, timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    print(f"     qc-updates --fix timed out on stage {n + 1}",
                          file=sys.stderr, flush=True)
                else:
                    blob = fix.stdout + fix.stderr
                    if fix.returncode != 0 or "fix FAILED" in blob or "CHECK" in blob:
                        note = next((l for l in blob.splitlines()
                                     if "fix FAILED" in l or "CHECK" in l), "")
                        print(f"     intermediate repair problem on stage {n + 1}: "
                              f"{note.strip() or f'exit {fix.returncode}'}",
                              file=sys.stderr, flush=True)
        for tmp in srcs[1:]:
            if os.path.exists(tmp):
                os.unlink(tmp)
        if proc.returncode == 0:
            print(f"ok   {os.path.basename(out)}", flush=True)
            ok += 1
        else:
            # bpy is noisy; the last non-Info line is the one that says why
            why = [l for l in proc.stderr.splitlines()
                   if l.strip() and not l.startswith(("Info:", "Timer", "Read blend"))]
            print(f"FAIL {os.path.basename(out)}  ({row['node_group']} "
                  f"{row['set_args']})\n     {why[-1] if why else 'no error output'}",
                  file=sys.stderr, flush=True)
            if os.path.exists(out):
                os.unlink(out)
            fail += 1

    if not args.dry:
        print(f"done: {ok} written, {fail} failed, {skip} skipped")


if __name__ == "__main__":
    main()
