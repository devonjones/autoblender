# Configuration notes

---

# Agents

Each H2 below names a reviewer. The one-line summary tells the main loop **what the reviewer checks and when to spawn it**. The body points at `.reviewers/<name>.md`, which the spawned Task reads as its complete specification.

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

All work goes through pull requests against `test` (the default branch); `main` only takes release PRs from `test`. Merge only after CI passes and the review loop is done.

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

autoblender is a Unix-style CLI (`ab`) over headless bpy, trimesh, and manifold3d, run through Docker (`bin/ab`). `src/autoblender/api.py` is also the handler of a container Lambda behind a function URL: the STL Tools desktop client (`bin/stl-tools.ps1`, Windows PowerShell 5.1 + WinForms) uploads STLs through S3 presigned URLs and runs `PROCESSES` on them. `terraform/` deploys it, reading ECR and CI-role outputs from `openforge-infra`.

Conventions: `import bpy` is deferred until a command needs it (7s import). Modules that don't need Blender (`union.py`, `cut.py`, `api.py`) must stay bpy-free. Tests are `_selfcheck()` functions run with `python -m autoblender.<module>` in the image; there is no pytest. Deliberate corner-cuts are marked with `ponytail:` comments naming the ceiling. PowerShell scripts stay 5.1-compatible and ASCII-only.

CI: `.github/workflows/deploy.yml` builds the `lambda` image target, pushes to ECR, and runs `tofu apply`: push to `test` deploys staging, push to `main` deploys production. No test workflow yet.
