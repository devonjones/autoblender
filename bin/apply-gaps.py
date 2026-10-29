#!/usr/bin/env python3
"""Work rows out of docs/option-gaps.tsv.

Applies the row's node group to its core and writes the result to the row's
out_path -- always under a texture's updates/ folder, never the production tree.

    bin/apply-gaps.py dungeon_stone              # do the work
    bin/apply-gaps.py dungeon_stone --dry        # print what it would run
    bin/apply-gaps.py dungeon_stone --option unsupported
    bin/apply-gaps.py rough_stone --family window+square

Skips rows with no node group, no core, a non-empty status, or a
likely_deliberate flag, plus any row whose output already exists -- so it is
resumable: run it again and it picks up where it stopped.
"""
import argparse
import csv
import os
import sys

import abrun

# The OpenForge cut plane sits at the base height, and where the stone texture
# is tangent to it the cut leaves gross paper-thin flanges instead of a clean
# face.  Dropping the mesh 0.02 first makes the plane bite into solid material.
# Devon passed the recut portcullis Q pieces by sight on 2026-09-22.
#
# Only the OpenForge groups get it: an OpenLock group makes no bottom cut, so
# nudging there would just shorten the piece by 0.02 for nothing.
NUDGE_Z = -0.02

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


def demo():
    """Self-check the row filter: `bin/apply-gaps.py --demo`."""
    base = {"texture": "t", "has_core": "yes", "likely_deliberate": "no",
            "node_group": "OpenLock", "status": "", "missing_option": "openlock+unsupported"}
    r = dict(base)
    assert wanted(r, "t", "openlock+unsupported", exact=True)
    assert wanted(r, "t", "openlock+unsupported", exact=False)
    side = dict(base, missing_option="openlock+unsupported,side")
    assert not wanted(side, "t", "openlock+unsupported", exact=True)
    assert wanted(side, "t", "openlock+unsupported", exact=False)
    assert wanted(r, "t", None) and wanted(side, "t", None)
    # every exclusion still applies under --exact
    for field, bad in (("texture", "other"), ("has_core", "no"),
                       ("likely_deliberate", "yes"), ("node_group", ""),
                       ("status", "REVIEWED: ok")):
        assert not wanted(dict(r, **{field: bad}), "t", "openlock+unsupported",
                          exact=True), field
    demo_build()
    print("apply-gaps demo ok")


def demo_build():
    """build() against a fake bin/ab: what counts as done, and what is left behind."""
    import tempfile
    real = abrun.AB
    with tempfile.TemporaryDirectory() as d:
        def fake(body):
            with open(abrun.AB, "w") as fh:
                fh.write("#!/bin/sh\n" + body)
            os.chmod(abrun.AB, 0o755)
        abrun.AB = os.path.join(d, "ab")
        out = os.path.join(d, "x.stl")
        stage = os.path.join(d, "x.stage1.stl")
        two = [["gn", "apply", "b", "G1", "core", stage], ["gn", "apply", "b", "G2", stage, out]]
        try:
            # exit 0 but nothing written is a failure, not "ok"
            fake("exit 0\n")
            assert "no mesh was written" in build(two[1:], ["core"], out)
            # a stage that crashes after writing a partial out leaves no out behind --
            # the next run would otherwise skip the row as done
            fake(f"printf partial > {out}\nexit 139\n")
            r = build(two[1:], ["core"], out)
            assert r.startswith("exit 139 (SIGSEGV)") and not os.path.exists(out), r
            # a failed intermediate repair stops the chain, and the stage file goes
            fake('case "$1" in *qc-updates.py) echo "x (fix FAILED)";; '
                 '*) printf %0100d 0 > "$6";; esac\nexit 0\n')
            r = build(two, ["core", stage], out)
            assert r.startswith("intermediate repair failed on stage 1"), r
            assert not os.path.exists(stage) and not os.path.exists(out)
            # a clean chain writes out and removes the intermediate
            fake('case "$1" in *qc-updates.py) echo ok;; *) printf %0100d 0 > "$6";; esac\n')
            assert build(two, ["core", stage], out) is None
            assert os.path.exists(out) and not os.path.exists(stage)
        finally:
            abrun.AB = real


def build(cmds, srcs, out):
    """Run one row's stage chain.  None on success, else the reason it failed.

    Stage intermediates and any partial `out` are removed however this exits --
    including Ctrl-C -- because a leftover `out` is taken as done by the next run.
    """
    done = False
    try:
        for n, cmd in enumerate(cmds):
            proc = abrun.run(cmd)
            if proc.returncode != 0:
                return abrun.why(proc)
            if n + 1 < len(cmds):
                # A float32 fin on an intermediate makes it non-manifold, and the
                # next stage's MANIFOLD boolean then returns empty geometry --
                # seen on OpenLock;Pegs.  qc-updates.py signals a failed repair
                # only by printing "(fix FAILED)"; it has no non-zero exit.
                fix = abrun.run([f"{abrun.REPO}/bin/qc-updates.py", srcs[n + 1], "--fix"],
                                python=True)
                blob = fix.stdout + fix.stderr
                if fix.returncode != 0 or "fix FAILED" in blob:
                    return f"intermediate repair failed on stage {n + 1}: {abrun.why(fix)}"
                note = next((l for l in blob.splitlines() if "CHECK" in l), "")
                if note:
                    print(f"     stage {n + 1}: {note.strip()}", file=sys.stderr, flush=True)
        if not os.path.exists(out) or os.path.getsize(out) <= 84:
            return "every stage exited 0 but no mesh was written"
        done = True
        return None
    finally:
        for tmp in srcs[1:] + ([] if done else [out]):
            if os.path.exists(tmp):
                os.unlink(tmp)


def main():
    if "--demo" in sys.argv:
        return demo()
    ap = argparse.ArgumentParser()
    ap.add_argument("texture")
    ap.add_argument("--option", help="only rows whose option contains this")
    ap.add_argument("--exact", action="store_true",
                    help="--option must EQUAL the row's option, not just be contained in "
                         "it.  'openlock+unsupported' otherwise also picks up "
                         "'openlock+unsupported,side', which is the difference between "
                         "the rows that need no ports and the ones waiting on a sculpt.")
    ap.add_argument("--family", help="only rows whose family ends with this, e.g. window+square")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--nudge-z", type=float, default=NUDGE_Z, metavar="MM",
                    help="override the OpenForge cut nudge.  Pass 0 when the cores were "
                         "already dropped by hand -- Devon drops the wall 0.02 into the "
                         "base when the union overlap is poor, which moves the wall "
                         "relative to the cut plane exactly as this nudge does, so "
                         "applying both stacks to 0.04.")
    args = ap.parse_args()

    in_family = lambda r: args.family is None or r["family"].endswith("/" + args.family)
    with open(TSV, newline="") as fh:
        queue = list(csv.DictReader(fh, delimiter="\t"))
    if not any(r["texture"] == args.texture and in_family(r) for r in queue):
        ap.error(f"no {args.texture} rows in family .../{args.family}")
    rows = [r for r in queue if wanted(r, args.texture, args.option, args.exact) and in_family(r)]

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
            # that this script does not implement.
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
            cmd = ["gn", "apply", blend, group, srcs[n],
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
        reason = build(cmds, srcs, out)
        if reason is None:
            print(f"ok   {os.path.basename(out)}", flush=True)
            ok += 1
        else:
            print(f"FAIL {os.path.basename(out)}  ({row['node_group']} "
                  f"{row['set_args']})\n     {reason}", file=sys.stderr, flush=True)
            fail += 1

    if not args.dry:
        print(f"done: {ok} written, {fail} failed, {skip} skipped")


if __name__ == "__main__":
    main()
