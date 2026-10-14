# Orchestrator notes

This directory is consumed by the `pr-review-loop` skill — do not run these reviewers directly. The skill owns posting findings, per-thread replies, retirement, CI gating, and exit conditions.

**No external review bots on this repo.** Gemini Code Assist and Cursor Bugbot are not installed (`config.json` `bots`); do not poll for them. `general-reviewer` is the broad-coverage substitute and spawns on every PR alongside the in-scope specialists.

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
| `terraform-reviewer` | `terraform/**/*.tf`, tofu steps of `.github/workflows/deploy.yml` |
| `clarity-reviewer` | `*.md`, docstrings and comments in `*.py` |
| `powershell-client-reviewer` | `bin/*.ps1`, `bin/*.cmd` |
| `lambda-api-reviewer` | `src/autoblender/{api,cut,union,io}.py`, `Dockerfile` `lambda` stage, `aws_lambda_*` in `terraform/main.tf` |

Each spec carries its own `**Severity:**` defaults; a reviewer may promote or demote a finding but must say why.

## Always do PRs

All work goes through pull requests against `test` (the default branch); `main` only takes release PRs from `test`. Merge only after CI passes and the review loop is done.

## Retired reviewers

- `code-reviewer` — its lens was quoting the exact CLAUDE.md rule, and this repo has no CLAUDE.md; its bug half duplicated `general-reviewer`.
- `type-design-analyzer` — the repo has two classes, both private helpers; the spec targeted other languages and used its own output format and severities.

# Context

autoblender is a Unix-style CLI (`ab`) over headless bpy, trimesh, and manifold3d, run through Docker (`bin/ab`). `src/autoblender/api.py` is also the handler of a container Lambda behind a function URL: the STL Tools desktop client (`bin/stl-tools.ps1`, Windows PowerShell 5.1 + WinForms) uploads STLs through S3 presigned URLs and runs `PROCESSES` on them. `terraform/` deploys it, reading the ECR repository and permissions-boundary outputs from `openforge-infra`.

Conventions (repeated in the specs that need them): `import bpy` is deferred until a command needs it (7s import). Modules that don't need Blender (`union.py`, `cut.py`, `api.py`) must stay bpy-free. Tests are `_selfcheck()` functions run with `python -m autoblender.<module>` in the image; there is no pytest. Deliberate corner-cuts are marked with `ponytail:` comments naming the ceiling. PowerShell scripts stay 5.1-compatible and ASCII-only.

CI: `.github/workflows/deploy.yml` builds the `lambda` image target, pushes to ECR, and runs `tofu apply`: push to `test` deploys staging, push to `main` deploys production. No test workflow yet.
