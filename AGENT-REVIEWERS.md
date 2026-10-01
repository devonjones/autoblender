# Configuration

```json
{
  "defaults_version_checked": "1.7.0",
  "disabled": [
    "silent-failure-hunter",
    "comment-analyzer",
    "pr-test-analyzer",
    "code-simplifier"
  ],
  "overlap_acknowledged": {
    "general-reviewer": {
      "overlaps_with": "code-reviewer",
      "reason": "Both read the whole diff, but the beats differ: code-reviewer audits CLAUDE.md compliance with a quote-the-exact-rule forcing function, general-reviewer stands in for the absent Gemini/Cursor bot pass (logic correctness verified empirically, design soundness, contract fidelity, docs accuracy). Keeping both is deliberate."
    }
  },
  "bots": {
    "gemini": false,
    "cursor": false
  }
}
```

---

# Agents

Each H2 below names a reviewer. The one-line summary tells the main loop **what the reviewer checks and when to spawn it**. The body points at `.reviewers/<name>.md`, which the spawned Task reads as its complete specification.

## general-reviewer

**What it checks:** the whole PR, generalist pass — logic correctness against docstrings and the PR body (verified by running the code in the image, not by plausibility), design soundness, cross-cutting interactions, docs accuracy, house style. Does NOT duplicate the specialists.
**When to spawn:** **every PR, every round.** Never skipped for scope; last to retire. Posts one structured PR comment.

Read `.reviewers/general-reviewer.md` and follow it as your complete review specification.

---

## error-handling-reviewer

**What it checks:** silent exception swallows, `return` inside `finally`, generic `except Exception` without re-raise, bare `except:`, missing `raise ... from`, `assert` used for runtime validation.
**When to spawn:** PR touches production `*.py`. Skip for docs / config / tofu-only diffs.

Read `.reviewers/error-handling-reviewer.md` and follow it as your complete review specification.

---

## logging-reviewer

**What it checks:** sensitive data in diagnostics (P1), `print()` to stdout where stdout carries data, chatty defaults, error output without the cause.
**When to spawn:** PR touches `*.py` or `bin/*` that prints diagnostics. Skip otherwise.

Read `.reviewers/logging-reviewer.md` and follow it as your complete review specification.

---

## resource-leak-reviewer

**What it checks:** `open()`/`urlopen`/subprocess without context managers, temp files not cleaned up, whole-file reads of unbounded external input, per-call client construction in loops.
**When to spawn:** PR touches `*.py` that allocates fds, sockets, temp files, or subprocesses.

Read `.reviewers/resource-leak-reviewer.md` and follow it as your complete review specification.

---

## external-process-reviewer

**What it checks:** missing `timeout=` on `subprocess.run`, `shell=True` with non-literal input (P1), no stderr capture on failure, undifferentiated failure modes.
**When to spawn:** PR diff uses `subprocess`, `os.system`, or `shutil.which`. Skip otherwise.

Read `.reviewers/external-process-reviewer.md` and follow it as your complete review specification.

---

## credentials-hygiene-reviewer

**What it checks:** literal secrets committed (P1), OAuth artifacts (`client_secret*.json`, token dumps) in the diff or image (P1), tokens in URLs, tokens echoed to output, refresh-token persistence outside Secrets Manager.
**When to spawn:** PR touches auth/token code, `bin/drive-auth`, `.gitignore`/`.dockerignore`, the Dockerfile, or tofu secrets. Cheap — when in doubt, spawn it.

Read `.reviewers/credentials-hygiene-reviewer.md` and follow it as your complete review specification.

---

## test-coverage-reviewer

**What it checks:** every touched function with non-trivial logic is exercised by a module `_selfcheck()` (this repo's convention; no pytest), the check actually fails when the logic breaks, bug fixes get a regression assertion.
**When to spawn:** PR modifies `src/autoblender/*.py` outside pure CLI wiring. Skip for docs / tofu / Dockerfile-only diffs.

Read `.reviewers/test-coverage-reviewer.md` and follow it as your complete review specification.

---

## complexity-reviewer

**What it checks:** McCabe > 10, nesting depth ≥ 4, parameter count > 5, nested ternaries, redundant single-call wrappers. **Introduced/worsened only; no borderline soft flags.**
**When to spawn:** PR touches production `*.py`. Skip for data / docs / config only.

Read `.reviewers/complexity-reviewer.md` and follow it as your complete review specification.

---

## dead-code-reviewer

**What it checks:** unused module-level symbols, commented-out code, partial refactors leaving the old name, `pass`-only stubs.
**When to spawn:** PR touches `*.py`, especially when it removes call sites or moves code. Skip for pure additive changes.

Read `.reviewers/dead-code-reviewer.md` and follow it as your complete review specification.

---

## terraform-reviewer

**What it checks:** this repo's tofu stays in the apps layer — no CloudFront/ACM/public DNS, no baseline resources, no reads from `openforge-infra-frontend` state, no hardcoded account ids outside backend config.
**When to spawn:** PR adds or modifies a `resource`, `data`, `module`, `provider`, or backend block under `terraform/**/*.tf`. Skip value-only edits and non-tofu diffs.

Read `.reviewers/terraform-reviewer.md` and follow it as your complete review specification.

---

## clarity-reviewer

**What it checks:** terseness and accuracy of markdown, docstrings, and comments; docstring claims that don't match the code.
**When to spawn:** PR touches `*.md` or changes docstrings/comments in `*.py`. Skip for pure-code diffs.

Read `.reviewers/clarity-reviewer.md` and follow it as your complete review specification.

---

# Guidelines

## How the pack runs

**This file is consumed BY the `pr-review-loop` skill — do not run these reviewers directly.** The skill owns posting findings as PR line comments, per-thread replies, per-agent retirement, CI gating, and exit conditions.

**No external review bots on this repo.** Gemini Code Assist and Cursor Bugbot are not installed; do not poll for them. `general-reviewer` is the broad-coverage substitute and spawns on every PR alongside the in-scope specialists.

Each reviewer runs independently. Silence from one reviewer is not an endorsement.

**Per-reviewer file scope:**

| Reviewer | Files in scope |
|----------|----------------|
| `general-reviewer` | everything |
| `error-handling-reviewer` | `*.py` (production code) |
| `logging-reviewer` | `*.py`, `bin/*` |
| `resource-leak-reviewer` | `*.py` |
| `external-process-reviewer` | `*.py`, `bin/*` using `subprocess` |
| `credentials-hygiene-reviewer` | everything (auth code, ignore files, Dockerfile, tofu) |
| `test-coverage-reviewer` | `src/autoblender/*.py` |
| `complexity-reviewer` | `*.py` |
| `dead-code-reviewer` | `*.py` |
| `terraform-reviewer` | `terraform/**/*.tf`, `.github/workflows/*.yml` |
| `clarity-reviewer` | `*.md`, docstrings and comments in `*.py` |

Skip reviewers whose file scope doesn't match the PR diff (`general-reviewer` always matches).

## Always do PRs

All work goes through pull requests — never push directly to `main`. Merge only after CI passes and the review loop is done.

## Severity convention

Every finding is tagged **P1** (blocking, fix in-PR), **P2** (should fix in this PR), or **P3** (advisory, deferrable with a ticket). Defaults per reviewer:

- `general-reviewer`: Critical/Medium/Minor → P1/P2/P3.
- `credentials-hygiene-reviewer`: P1.
- `resource-leak-reviewer`: P1.
- `error-handling-reviewer`: P2; P1 for silent swallows and `return` in `finally`.
- `logging-reviewer`: P2; P1 for secrets in output.
- `external-process-reviewer`: P2; P1 for `shell=True` injection.
- `test-coverage-reviewer`: P2.
- `complexity-reviewer`: P2 for objective floors; P3 for heuristics.
- `dead-code-reviewer`: P3.
- `terraform-reviewer`: P1 for layering violations; P2 for hardcoded values.
- `clarity-reviewer`: P3.

A reviewer may promote or demote a finding from its default but must say why. Where a spec under `.reviewers/` tags a pattern with an explicit priority, the spec wins.

## Output format

```
[<reviewer>] [<severity>] <one-line title>

File: path/to/file:LINE
Quote:
    <1-5 lines being flagged>

Issue: <what is wrong>
Suggested fix:
    <concrete diff or rewrite>
```

# Context

autoblender is a Unix-style CLI (`ab`) over headless bpy, trimesh, and manifold3d, run through Docker (`bin/ab`). `src/autoblender/drive.py` is also the handler of a container Lambda that watches a Google Drive folder tree and runs `PROCESSES` on dropped STLs; `terraform/` deploys it on a 1-minute EventBridge rate, reading ECR and CI-role outputs from `openforge-infra`.

Conventions: `import bpy` is deferred until a command needs it (7s import). Modules that don't need Blender (`union.py`, `cut.py`, `drive.py`) must stay bpy-free. Tests are `_selfcheck()` functions run with `python -m autoblender.<module>` in the image; there is no pytest. Deliberate corner-cuts are marked with `ponytail:` comments naming the ceiling.

CI: `.github/workflows/deploy.yml` builds the `lambda` image target, pushes to ECR, and runs `tofu apply` for staging on push to `main`. No test workflow yet.
