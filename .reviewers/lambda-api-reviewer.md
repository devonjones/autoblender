# lambda-api-reviewer
model: sonnet
verification: mutation — runs the probes below in the image (scratch copy for any edit) and quotes exit code and output.
**Checks:** api.py on Lambda: bpy-free and boto3-lazy imports, PROCESSES registry consistent, auth before every route, function-URL and Lambda limits respected.
**Spawn when:** diff touches `src/autoblender/{api,cut,union,io}.py`, the Dockerfile `lambda` stage, or `aws_lambda_*` blocks in `terraform/main.tf`. Skip otherwise.
**Contents:** What to flag · Do NOT flag · Probes

The handler (`autoblender.api.handler`) runs in a warm-reused container behind a function URL; a merge to `test` deploys it to staging.

## What to flag

- **bpy reaches the handler (P1):** any import, direct or transitive, of `bpy`/`mathutils`/`bmesh` from `api.py`, `cut.py`, `union.py`, `io.py`.
- **boto3 at module import (P2):** boto3 is only in the `lambda` image; importing it at module top breaks `python -m autoblender.api` in the CLI image. It stays inside `_s3()` / `_api_key()`.
- **Registry drift (P1):** a `PROCESSES` entry whose options aren't parameters of its `fn`, whose options aren't booleans, or whose `fn` isn't importable without bpy. `_check_registry()` must still pass.
- **Auth (P1):** any route reachable before the `hmac.compare_digest` key check in `route()`, or a change that lets an empty secret match (`_api_key()` must fail closed).
- **Limits (P2):** a response body that can exceed the 6 MB function-URL limit instead of going through a presigned URL; a job that can outlive the Lambda `timeout` (900 s) or fill the 2048 MB `ephemeral_storage` in `terraform/main.tf`; `URL_TTL` shorter than a realistic upload.

## Do NOT flag

- `/tmp` and fd leaks — resource-leak-reviewer.
- Generic exception style — error-handling-reviewer.
- Client-side changes — powershell-client-reviewer.

## Probes

The local `autoblender:latest` may predate `api.py`, so mount the source over it:

- `docker run --rm -v "$PWD/src:/src:ro" -e PYTHONPATH=/src --entrypoint python autoblender:latest -c "import sys; sys.modules['bpy']=None; import autoblender.api, autoblender.cut, autoblender.union, autoblender.io"` → must exit 0 (also proves no top-level boto3: the CLI image has none).
- `docker run --rm -v "$PWD/src:/src:ro" -e PYTHONPATH=/src --entrypoint python autoblender:latest -m autoblender.api` → must end `api selfcheck ok`, exit 0. The tracebacks it prints on the way are expected.
