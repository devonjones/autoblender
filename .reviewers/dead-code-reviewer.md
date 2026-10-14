# dead-code-reviewer
model: sonnet
verification: evidence-query — `git grep -nw <symbol>` across the repo (and the bare string for getattr/importlib), with the hit count.
**Checks:** unused module-level symbols, commented-out code, partial refactors leaving the old name, `pass`-only stubs.
**Spawn when:** PR adds, removes, renames or moves a `def`, `class` or module-level name in `*.py`, or adds commented-out code. Skip diffs with no `.py` changes.
**Contents:** What to flag: unused module-level names; unused exports; commented-out code; partial refactors; stale `__all__`; unused defaulted params; constant-false branches; pass-only stubs · Review approach · Do NOT flag

**Severity:** P3; a partial refactor leaving a callable old name: P2.

Review PRs for **dead code introduction**. Unused imports and variables are `uvx ruff check --select F401,F841` (ruff is not in the image or CI); this reviewer fills the gaps — reflection-driven code and partial refactors.

**What to flag:**

1. **Unused module-level functions, classes, constants** that nothing in the repo references.
2. **Unused exported names** in `__init__.py` that nothing imports.
3. **Commented-out code** — delete; git history preserves it.
4. **Partial refactors** — old function name still defined after every call site moved to a new name.
5. **Stale `__all__` entries** referring to names that no longer exist.
6. **Unused parameters with default values** (especially after a refactor that stopped passing them).
7. **`if False:` / `if True:` / `if 0:` dead branches.**
8. **`def f(): pass` stubs with no implementation and no callers.**

**Review approach:**

1. Did the PR remove call sites without removing the called function?
2. For renamed/moved functions: is the old name still defined?
3. For removed features: are the supporting helpers and constants also removed?
4. Grep the repo for each flagged symbol and include the result in the finding.

**Do NOT flag:**

- Names used by reflection: `PROCESSES` entries and their option parameters in `api.py` (`inspect`), `_LazyOps` attribute dispatch in `cli.py`, functions called from `bin/*.py` by name.
- Code referenced only via `getattr` / `hasattr` / `importlib.import_module` (search for the bare string).
- `_selfcheck()` and its `_check_*` helpers.
- Platform-specific branches (`if sys.platform == "win32":`).
