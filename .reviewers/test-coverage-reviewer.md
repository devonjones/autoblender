# test-coverage-reviewer
model: sonnet
verification: mutation — breaks the touched logic in a scratch copy (never the working tree) and runs the module selfcheck in the image; a selfcheck that still passes is the finding.
**Checks:** every touched function with non-trivial logic is exercised by a module `_selfcheck()` (this repo's convention; no pytest), the check actually fails when the logic breaks, bug fixes get a regression assertion.
**Spawn when:** PR modifies `src/autoblender/*.py` outside pure CLI wiring. Skip for docs / tofu / Dockerfile-only diffs.
**Contents:** Core rule · Rules to enforce: Coverage required for all touched code; Refactors preserve coverage; New behavior needs new assertions; The selfcheck convention; Bug fix regression · Glue code · Selfcheck that cannot fail · Review approach · What to flag · Do NOT allow · Acceptable exceptions

**Severity:** P2.

**Core rule: code touched by a PR must be covered. Either the coverage already exists, or this PR adds it.** The unit of obligation is coverage of the touched code, not net-new checks for every diff: a pure rename of a covered function needs nothing; a new branch in it does.

**Rules to enforce:**

1. **Coverage required for all touched code.** For any modified or new function, verify that some selfcheck exercises it. If it is uncovered today, this PR adds coverage, or files a beads ticket before merge.
2. **Refactors preserve coverage.** Pure refactors need no new assertions if existing selfchecks still exercise the code and pass. Flag a refactor only when the touched code was uncovered before it.
3. **New behavior needs new assertions.** A new branch, exception path, or output shape in an already-covered function needs an assertion that hits it.
4. **The selfcheck convention.** There is no pytest and no `tests/`. Each module with non-trivial logic has a `_selfcheck()` under `if __name__ == "__main__":` (`union.py`, `cut.py`, `api.py`, `ops.py`), run as `python -m autoblender.<module>` inside the image. A pure function's selfcheck asserts on synthetic inputs (trimesh primitives, fake file lists). Valid coverage is an `assert` inside a module `_selfcheck()` (or a `_check_*` helper it calls) that exercises the touched function.
5. **Bug fix regression.** A fix needs an assertion that would have failed before it, and a commit message or comment saying what was broken.

**Glue code:** a function with no branching logic (a CLI subcommand that parses args and calls one function) is covered by the selfcheck of what it calls. Code with its own branching, validation, or parsing is not.

**Selfcheck that cannot fail:** a new assertion that still passes when the touched logic is broken. Prove it: break the logic in a scratch copy and run `docker run --rm -v "<copy>/src:/src:ro" -e PYTHONPATH=/src --entrypoint python autoblender:latest -m autoblender.<module>` (the mount covers a stale local image); report the exit code.

**Review approach:**

1. Identify all functions added or modified in the PR.
2. For each, grep the module's `_selfcheck()` and the `_check_*` helpers it calls for calls to it.
3. For modifications, verify the new behavior path is asserted, not just executed.
4. Distinguish "no coverage" and "coverage misses the new branch" (flag) from "pure refactor of covered code" (allow).
5. For each new or modified selfcheck, confirm it asserts on output, not only that the call returns.

**What to flag:**

- New functions with non-trivial logic and no selfcheck coverage.
- Modified functions where the new branch isn't asserted.
- New exception paths with no assertion that triggers them.
- Edge cases visible in the code (`None`, empty meshes, boundary values) without assertions.

**Do NOT allow:**

- "Verified manually via CLI" as a substitute when the unit has branching logic.
- Marking coverage "out of scope" without a beads ticket.

**Acceptable exceptions (each needs a beads ticket before merge):**

- Covering legacy uncovered code is genuinely larger than this PR.
- The change is a config/data edit with no executable logic, or a dependency bump.
