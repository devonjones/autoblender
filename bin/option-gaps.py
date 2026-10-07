#!/usr/bin/env python3
"""Survey the OpenForge release tree for missing connection-option variants.

Read-only against Dropbox. Writes a TSV work queue into the autoblender repo.

A gap is an option with no file, found three ways (the gap_class column):
  family       -- the option exists for another variant in the same family
                  (the directory holding the option subfolders);
  family_type  -- it exists for the same family path in another texture;
  canonical    -- the hard floor: openlock implies openlock+unsupported, and the
                  pegs variants follow from the openlock ones.
"""
import collections
import csv
import os
import re
import sys

HOME = os.path.expanduser("~")
RELEASE = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForge/tiles"
DEV = f"{HOME}/Dropbox/projects/Hardware/objects/OpenForgeDevelopment/tiles"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "option-gaps.tsv")

COLUMNS = ("texture", "family", "variant", "missing_option", "gap_class",
           "likely_deliberate", "has_core", "core_path", "node_group",
           "set_args", "out_path", "status")

# docs/connection-options.md
_MAPPING_RAW = {
    "openforge":                  ("OpenForge", ""),
    "openforge,side":             ("OpenForge+Side", "Lock Style=OpenLock"),
    "openforge,side+dragonlock":  ("OpenForge+Side", "Lock Style=DragonLock"),
    "openlock":                   ("OpenLock", ""),
    "openlock+unsupported":       ("OpenLock", "OpenLock Supports=False"),
    "openlock,side":              ("OpenLock", "Side Locks=True"),
    "openlock+unsupported,side":  ("OpenLock", "OpenLock Supports=False;Side Locks=True"),
}
# blueprint/ is the temple prep area: copies of cores under the same names,
# sometimes already cut -- never a source.
SKIP_TEXTURES = {"blueprint"}
# hard floor: openlock present => openlock+unsupported expected
CANONICAL = ("openlock", "openlock+unsupported")
_CORNER_LOCK_STYLE_RAW = {"openforge,side": "OpenLock",
                          "openforge,side+dragonlock": "DragonLock",
                          "openforge,side+filament": "Filament"}


def tokens(name):
    return set(t for t in re.split(r"[,+]", name) if t)


def canon(option):
    """Canonical form of a connection field.

    The connection is an unordered, comma-joined SET of tags, each a thing with
    '+' options hung off it -- so `openforge,magnetic` and `magnetic,openforge`
    are the same connection.  Grammar per the catalog's own parser, in
    ~/MasterworkTools/openforge-catalog/openforge/data/scanner.py
    (parse_connection / parse_filename).  Sorting both levels makes equal
    connections compare equal, and normalises the spelling we emit.
    """
    tags = []
    for tag in option.split(","):
        if not tag:
            continue
        head, *opts = tag.split("+")
        tags.append("+".join([head] + sorted(opts)))
    return ",".join(sorted(tags))


def spell(option):
    """How a connection is written in a FILENAME: canon() order, but `pegs` last.

    canon() stays the identity key (statuses, comparisons).  Filenames follow the
    release tree, which writes `side,pegs` -- 142 released files, none `pegs,side`,
    and every option folder is `side,pegs` (openforge-renamer ruling, 2026-09-29).
    """
    tags = option.split(",")
    return ",".join([t for t in tags if t != "pegs"] + [t for t in tags if t == "pegs"])


def is_corner_family(family):
    return "corner" in family.lower()


def mapping_for(option, family, variant=""):
    """(node_group, set_args).  Corners and +filament go through Corner.Left.

    A `+low` piece must also set Low=True: the groups place side locks at
    full-wall height otherwise, leaving them standing ~17.5mm proud of a low
    wall.  `+low` is part of the piece name, not the option, so it cannot come
    from the option->settings table alone.
    """
    if option == canon("openforge,side+filament") or (
            is_corner_family(family) and option in CORNER_LOCK_STYLE):
        group = "Corner.Left"
        args = "Port Block=False;Lock Style=%s" % CORNER_LOCK_STYLE[option]
    else:
        toks = option.split(",")
        if "pegs" in toks:
            # pegs are a second pass: build the base variant, then apply the Pegs
            # group (which unions in that library's Pegs-<L>-Wall object).  A ';'
            # in node_group means "chain these groups in order".
            base = canon(",".join(t for t in toks if t != "pegs"))
            group, args = MAPPING.get(base, ("", ""))
            if group:
                group = f"{group};Pegs"
        else:
            group, args = MAPPING.get(option, ("", ""))

    # Test the FIRST stage: a `;Pegs` chain still drives the base group, and
    # testing the whole string meant a `+low` pegs row silently got no Low=True.
    first = group.split(";")[0]
    if first in LOW_AWARE and "+low" in variant:
        args = ";".join(filter(None, [args, "Low=True"]))
        # These groups carry TWO lock-style sockets, `Lock Style` and `Lock Style
        # (Low)`, and `Low=True` only selects which one is read.  Setting the
        # full-height socket on a low wall left the low one at its default, so a
        # `+low` DragonLock side file would have been built with OpenLock side
        # locks.  Checked across all five libraries: OpenForge+Side and
        # Corner.Left/Right have both sockets, OpenLock has neither (it takes
        # `Low` alone), so the rename is scoped to the two that do.
        if first in LOCK_STYLE_LOW:
            args = args.replace("Lock Style=", "Lock Style (Low)=")
    return group, args


# groups with a `Low` input socket, and the subset that also has `Lock Style (Low)`
LOW_AWARE = ("OpenLock", "OpenForge+Side", "Corner.Left", "Corner.Right")
LOCK_STYLE_LOW = ("OpenForge+Side", "Corner.Left", "Corner.Right")

MAPPING = {canon(k): v for k, v in _MAPPING_RAW.items()}
CORNER_LOCK_STYLE = {canon(k): v for k, v in _CORNER_LOCK_STYLE_RAW.items()}
CANONICAL = (canon(CANONICAL[0]), canon(CANONICAL[1]))


def scan_release():
    """-> (records, unparsed) where a record is (family, chain, variant, option)."""
    staged, unparsed = [], []
    for dirpath, _dirs, files in os.walk(RELEASE):
        for fn in files:
            if not fn.endswith(".stl"):
                continue
            parts = fn[:-4].split(".")
            if len(parts) < 2:
                unparsed.append(os.path.join(dirpath, fn))
                continue
            option = canon(parts[-1])
            variant = ".".join(parts[:-1])
            rel = os.path.relpath(dirpath, RELEASE).split(os.sep)
            # walk up while the directory name is a token-subset of the option
            chain = []
            opt_tok = tokens(option)
            while len(rel) > 1 and len(chain) < 3 and tokens(rel[-1]) <= opt_tok:
                chain.insert(0, rel.pop())
            staged.append(("/".join(rel), tuple(chain), variant, option,
                           os.path.join(dirpath, fn)))

    # An option string is "real" if it was seen as a folder chain somewhere.
    vocab = {o for _f, c, _v, o, _p in staged if c}
    chains = collections.defaultdict(collections.Counter)
    for _f, c, _v, o, _p in staged:
        if c:
            chains[o][c] += 1

    records = []
    for family, chain, variant, option, path in staged:
        if not chain and option not in vocab:
            unparsed.append(path)
            continue
        records.append((family, chain, variant, option))
    return records, unparsed, vocab, chains


def index_cores():
    """-> (by_prefix, by_texture) core indexes.

    Dev uses two naming conventions: '<set>#<piece>.<size>.<LETTER>.core.stl'
    (matches the release prefix directly) and, in several textures, a bare
    '<piece>.<...>.core.stl' under the texture folder.  Index both.
    """
    by_prefix = collections.defaultdict(list)
    by_texture = collections.defaultdict(list)
    for dirpath, _dirs, files in os.walk(DEV):
        texture = os.path.relpath(dirpath, DEV).split(os.sep)[0]
        if texture in SKIP_TEXTURES:
            continue
        for fn in files:
            if fn.endswith(".core.stl"):
                full = os.path.join(dirpath, fn)
                by_prefix[fn.split(".")[0]].append((fn, full))
                by_texture[texture].append((fn, full))
    return by_prefix, by_texture


def _match(cands, prefix, want):
    for fn, full in sorted(cands):
        comps = fn[: -len(".core.stl")].split(".")
        if comps[0] == prefix and all(w in comps[1:] for w in want):
            return full
    return ""


def find_core(idx, variant, texture):
    """variant is '<set>#<piece>.<LETTER>' (the letter part may hold dots)."""
    by_prefix, by_texture = idx
    prefix, _, key = variant.partition(".")
    if not key:
        return ""
    want = key.split(".")
    hit = _match(by_prefix.get(prefix, ()), prefix, want)
    if hit:
        return hit
    # fallback: dev drops the '<texture>#' and files sit under the texture folder
    piece = prefix.split("#", 1)[1] if "#" in prefix else prefix
    return _match(by_texture.get(texture, ()), piece, want)


def write_tsv(path, header, rows):
    """Replace a TSV atomically: write a sibling temp file, then os.replace.

    docs/option-gaps.tsv carries the `status` column, which is hand-entered
    judgement with probe evidence in it and cannot be regenerated.  Three
    scripts used to rewrite this file with open(path, "w"), which truncates
    first -- a crash, a full disk or a Ctrl-C mid-write left a half-written
    queue and lost that work.  os.replace is atomic on the same filesystem, so
    the old file survives intact until the new one is complete.

    The temp file is a sibling (not /tmp) so the rename stays within one
    filesystem; across filesystems os.replace falls back to a copy and loses
    the atomicity this exists to provide.
    """
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".option-gaps.", suffix=".tsv")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.writer(fh, delimiter="\t", lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())       # a crash after the rename must not leave it empty
        try:                            # mkstemp makes 0600; keep the file's own mode
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def prior_status(path):
    """Statuses the user has marked, keyed by identity, so a regeneration keeps
    them.  Without this every re-run would silently discard the accept/reject
    work, which is the only human-supplied column in the file."""
    survey, seeded = {}, {}
    try:
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                if r.get("status"):
                    into = seeded if r.get("gap_class") == "seeded" else survey
                    into[(r["texture"], r["family"], r["variant"],
                          r["missing_option"])] = r["status"]
    except FileNotFoundError:
        pass
    return {**seeded, **survey}     # a row present twice: a non-empty survey status wins


def prior_seeded(path):
    """Rows bin/seed-gaps.py wrote.  They come from dev cores, not the release
    tree, so the survey cannot regenerate them -- carry them over verbatim.

    Re-ordered through COLUMNS rather than taken as `list(r.values())`, which
    follows whatever order the file on disk happens to have.  These rows are the
    one part of the queue that cannot be rebuilt from the tree, so a header that
    had drifted would scramble exactly the data with no other source.  A file
    whose header does not match COLUMNS is refused rather than silently
    reinterpreted.
    """
    try:
        with open(path, newline="") as fh:
            rd = csv.DictReader(fh, delimiter="\t")
            if rd.fieldnames and tuple(rd.fieldnames) != tuple(COLUMNS):
                raise SystemExit(
                    f"{path}: header does not match COLUMNS; refusing to carry "
                    f"seeded rows over.\n  on disk: {rd.fieldnames}\n  expected: "
                    f"{list(COLUMNS)}")
            return [[r[c] for c in COLUMNS] for r in rd
                    if r["gap_class"] == "seeded"]
    except FileNotFoundError:
        return []


def drop_surveyed(rows, seeded):
    """Seeded rows the survey now emits itself (the texture shipped).  Keeping
    both would list the row twice, and the stale seeded status would compete
    with the one being edited."""
    have = {tuple(r[:4]) for r in rows}
    return [r for r in seeded if tuple(r[:4]) not in have]


def main():
    records, unparsed, _vocab, chains = scan_release()
    kept = prior_status(OUT)
    seeded = prior_seeded(OUT)
    cores = index_cores()

    have = collections.defaultdict(set)          # (family, variant) -> options
    fam_variants = collections.defaultdict(set)  # family -> variants
    fam_chain = collections.defaultdict(dict)    # family -> option -> chain
    for family, chain, variant, option in records:
        have[(family, variant)].add(option)
        fam_variants[family].add(variant)
        if chain or option not in fam_chain[family]:
            fam_chain[family].setdefault(option, chain)

    fam_options = {f: set().union(*(have[(f, v)] for v in vs))
                   for f, vs in fam_variants.items()}
    # family type = family path minus the leading texture component
    ftype_options = collections.defaultdict(set)
    for f, opts in fam_options.items():
        ftype_options[f.split("/", 1)[1] if "/" in f else ""] |= opts

    rows = []
    for family, variants in fam_variants.items():
        texture, _, sub = family.partition("/")
        ftype = sub
        fam_opts = fam_options[family]
        type_opts = ftype_options[ftype] - fam_opts
        # Unsupported crosses with EVERY openlock option the family has, not just
        # the bare one: `openlock,side` implies `openlock+unsupported,side`, and
        # `openlock,pegs` implies `openlock+unsupported,pegs`.  None of these
        # combinations exist anywhere in the tree, so no empirical tier can
        # propose them -- they have to be stated.
        peg_opts = set()
        for o in fam_opts | type_opts:
            toks = o.split(",")
            if CANONICAL[0] in toks:
                peg_opts.add(canon(",".join(
                    CANONICAL[1] if t == CANONICAL[0] else t for t in toks)))
        peg_opts -= fam_opts | type_opts
        for option in sorted(fam_opts | type_opts | peg_opts | {CANONICAL[1]}):
            for variant in sorted(variants):
                if option in have[(family, variant)]:
                    continue
                if option in fam_opts:
                    gap_class = "family"
                    n_missing = sum(1 for v in variants if option not in have[(family, v)])
                    # Deliberate at both ends of the range: missing from exactly one
                    # variant means that profile has no room for the hardware; present
                    # for exactly one means it was a scoped decision (aztlan doors got
                    # side locks on D only). Evidence of intent either way.
                    n_present = len(variants) - n_missing
                    deliberate = ("yes" if len(variants) > 2
                                  and (n_missing == 1 or n_present == 1) else "no")
                elif option in type_opts:
                    gap_class, deliberate = "family_type", "no"
                elif option in peg_opts:
                    gap_class, deliberate = "canonical", "no"
                elif option == CANONICAL[1] and CANONICAL[0] in have[(family, variant)]:
                    gap_class, deliberate = "canonical", "no"
                else:
                    continue
                chain = fam_chain[family].get(option) or (
                    chains[option].most_common(1)[0][0] if chains.get(option) else None)
                if chain is None and option in peg_opts:
                    # A brand new option (unsupported + pegs) has no folder to copy.
                    # Mirror the family's own layout for the supported equivalent --
                    # `openlock,pegs,side` -> `openlock/side,pegs` here, but nested
                    # as `openlock/pegs/side,pegs` in some textures -- and swap the
                    # leading folder for the unsupported one.  Synthesising the
                    # chain from segments got this wrong.
                    supported = canon(option.replace(CANONICAL[1], CANONICAL[0]))
                    ref = (fam_chain[family].get(supported)
                           or (chains[supported].most_common(1)[0][0]
                               if chains.get(supported) else None))
                    base = canon(",".join(t for t in option.split(",") if t != "pegs"))
                    base_chain = (fam_chain[family].get(base)
                                  or (chains[base].most_common(1)[0][0]
                                      if chains.get(base) else (base,)))
                    if ref:
                        chain = (base_chain[0],) + tuple(ref[1:])
                    else:
                        chain = base_chain + ("pegs",)
                chain = chain or (option,)
                node_group, set_args = mapping_for(option, family, variant)
                core = find_core(cores, variant, texture)
                out = "/".join(p for p in (DEV, texture, "updates", sub,
                                           *chain, f"{variant}.{spell(option)}.stl") if p)
                rows.append([texture, family, variant, option, gap_class, deliberate,
                             "yes" if core else "no", core, node_group, set_args,
                             out, kept.get((texture, family, variant, option), "")])

    order = {"family": 0, "family_type": 1, "canonical": 2}
    rows.sort(key=lambda r: (r[6] != "yes", r[5] != "no", order[r[4]],
                             r[0], r[1], r[2], r[3]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    seeded = drop_surveyed(rows, seeded)
    write_tsv(OUT, COLUMNS, rows + seeded)

    print(f"wrote {len(rows)} rows (+{len(seeded)} seeded) to {OUT}", file=sys.stderr)
    print(f"unparsed files: {len(unparsed)}", file=sys.stderr)
    return rows, unparsed


def demo():
    assert tokens("openforge,side+dragonlock") == {"openforge", "side", "dragonlock"}
    assert tokens("side,dragonlock") <= tokens("openforge,side+dragonlock")
    assert not tokens("window#corner+s2w") <= tokens("openforge,side+filament")
    assert mapping_for("openlock+unsupported,side", "aztlan/x") == (
        "OpenLock", "OpenLock Supports=False;Side Locks=True")
    assert mapping_for("openforge,side+filament", "aztlan/x")[0] == "Corner.Left"
    assert mapping_for("openforge,side", "aztlan/s2w/corner/window#corner+s2w") == (
        "Corner.Left", "Port Block=False;Lock Style=OpenLock")
    # +low must drive the LOW lock-style socket, not the full-height one
    g, a = mapping_for(canon("openforge,side+dragonlock"), "x/wall+low",
                       "t#wall+low.A")
    assert g == "OpenForge+Side" and "Lock Style (Low)=DragonLock" in a, a
    assert "Lock Style=DragonLock" not in a, a
    assert "Low=True" in a, a
    # a full-height wall is untouched
    g, a = mapping_for(canon("openforge,side+dragonlock"), "x/wall", "t#wall.A")
    assert a == "Lock Style=DragonLock", a
    # a +low corner drives the low socket too
    g, a = mapping_for(canon("openforge,side+dragonlock"), "x/corner+s2w",
                       "t#corner,wall+low.BA")
    assert g == "Corner.Left" and "Lock Style (Low)=DragonLock" in a and "Low=True" in a, a
    # a +low pegs chain still gets Low=True despite the ";Pegs" suffix
    g, a = mapping_for(canon("openlock,pegs"), "x/wall+low", "t#wall+low.A")
    assert g.startswith("OpenLock;") and "Low=True" in a, (g, a)
    # OpenLock has no (Low) lock-style socket, so it must NOT be renamed
    assert "Lock Style (Low)" not in a, a
    assert mapping_for("dragonlock", "aztlan/x") == ("", "")
    assert spell(canon("openlock+unsupported,side,pegs")) == "openlock+unsupported,side,pegs"
    assert spell(canon("openforge,side+dragonlock")) == "openforge,side+dragonlock"

    # a texture that ships stops being seeded: the survey row replaces it, and the
    # survey copy's status is the one that survives a regeneration
    import tempfile
    pad = [""] * (len(COLUMNS) - 4)
    survey = [["t", "f", "v", "o", *pad]]
    seeded = [["t", "f", "v", "o", *pad], ["t", "f", "w", "o", *pad]]
    assert [r[2] for r in drop_surveyed(survey, seeded)] == ["w"]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "q.tsv")
        row = dict.fromkeys(COLUMNS, "")
        row.update(texture="t", family="f", variant="v", missing_option="o")
        write_tsv(path, COLUMNS, [[*{**row, "gap_class": "family", "status": "NEW"}.values()],
                                  [*{**row, "gap_class": "seeded", "status": "OLD"}.values()]])
        assert prior_status(path) == {("t", "f", "v", "o"): "NEW"}, prior_status(path)
        # the rewrite keeps the file's mode (mkstemp alone would leave 0600)
        os.chmod(path, 0o644)
        write_tsv(path, COLUMNS, [])
        assert os.stat(path).st_mode & 0o777 == 0o644, oct(os.stat(path).st_mode)
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
