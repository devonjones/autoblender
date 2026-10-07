"""Is each matched .core.stl a real core, or a finished openforge body renamed?

A real core is the full-height wall: the OpenForge variant is the same part with
the bottom 6mm cut off, so a genuine core stands ~6mm taller than its shipped
openforge sibling.  A "core" that matches the openforge part exactly is not a
core at all -- it is the finished output under a misleading name, and building
an openlock variant from it yields a wall missing its bottom 6mm.

    AB_ENTRYPOINT=python bin/ab bin/audit-cores.py [--mark]

--mark writes a blocking status into docs/option-gaps.tsv for every row whose
core fails, so the queue cannot hand that row out as actionable again.
"""
import csv, importlib.util, os, sys, trimesh, collections

R = "/home/devon/Dropbox/projects/Hardware/objects/OpenForge/tiles"
TSV = "/work/docs/option-gaps.tsv"          # this script only runs in the container
# share option-gaps.py's atomic writer rather than truncating the queue in place
_spec = importlib.util.spec_from_file_location("og", "/work/bin/option-gaps.py")
og = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(og)

with open(TSV, newline="") as fh:
    rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["has_core"] == "yes"]
seen, verdict = {}, collections.Counter()
for r in rows:
    core = r["core_path"]
    if core in seen:
        continue
    fam = os.path.join(R, r["family"])
    # the family's shipped openforge file for this same variant, if there is one
    ref = os.path.join(fam, "openforge", f"{r['variant']}.openforge.stl")
    if not os.path.exists(ref):
        seen[core] = "no openforge reference"; verdict["no reference"] += 1; continue
    try:
        c, s = trimesh.load(core, force="mesh"), trimesh.load(ref, force="mesh")
    except Exception as e:
        # str(e) is empty for several exception types, so name the class too --
        # otherwise a defect in this script reads exactly like a bad STL
        seen[core] = f"load error {type(e).__name__}: {e or '(no detail)'}"
        verdict["load error"] += 1; continue
    dv = abs(c.volume - s.volume) / max(s.volume, 1e-9)
    dz = abs((c.bounds[1][2] - c.bounds[0][2]) - (s.bounds[1][2] - s.bounds[0][2]))
    if dv < 0.005 and dz < 0.05:
        seen[core] = "IS the openforge body"; verdict["openforge body (unusable as core)"] += 1
    else:
        seen[core] = f"differs (vol {dv*100:.1f}%, h {dz:.2f}mm)"; verdict["looks like a real core"] += 1

if "--mark" in sys.argv:
    with open(TSV, newline="") as fh:
        data = list(csv.reader(fh, delimiter="\t"))
    hdr, body = data[0], data[1:]
    i = {k: n for n, k in enumerate(hdr)}
    n = 0
    for r in body:
        if seen.get(r[i["core_path"]]) == "IS the openforge body" and not r[i["status"]]:
            r[i["status"]] = "blocked: core_path is the openforge body, not a core"
            n += 1
    og.write_tsv(TSV, hdr, body)
    print(f"marked {n} rows blocked\n")

for k, v in verdict.most_common():
    print(f"{v:4d}  {k}")
print("\n-- per core --")
for core, why in sorted(seen.items()):
    print(f"  {why:34s} {core.split('/tiles/')[-1]}")
