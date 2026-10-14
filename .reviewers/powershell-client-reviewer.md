# powershell-client-reviewer
model: sonnet
verification: evidence-query — runs the byte grep or syntax grep below and quotes the hit; API-contract findings quote both the client call and the api.py / terraform line.
**Checks:** `bin/stl-tools.ps1` / `bin/stl-tools.cmd` stay Windows PowerShell 5.1-compatible, ASCII-only, and match api.py's routes, fields and the Lambda's limits.
**Spawn when:** diff touches `*.ps1` or `*.cmd`. Skip otherwise.
**Contents:** What to flag · Do NOT flag

The STL Tools desktop client runs on stock Windows PowerShell 5.1 (WinForms, `System.Net.Http.HttpClient`). Anything 6+/7-only breaks every user.

## What to flag

- **Non-ASCII bytes (P1):** 5.1 reads BOM-less scripts as ANSI; smart quotes and em dashes corrupt strings or cause parse errors. Proof: `LC_ALL=C grep -nP '[^\x00-\x7F]' <file>`.
- **7-only syntax (P1):** `??`, `??=`, `?.`, `a ? b : c`, `&&`/`||` between pipelines, `ForEach-Object -Parallel`, `clean {}` blocks.
- **6+/7-only parameters (P1):** `ConvertFrom-Json -AsHashtable`/`-Depth`, `Invoke-RestMethod -SkipCertificateCheck`/`-StatusCodeVariable`/`-Form`/`-Authentication`, `Get-Content -AsByteStream`, `Join-Path` with more than two paths.
- **5.1 behaviour traps (P2):** `Invoke-WebRequest` without `-UseBasicParsing`; `Out-File`/`Set-Content` without `-Encoding` (5.1 defaults to UTF-16/ANSI); removing or moving the `[Net.ServicePointManager]::SecurityProtocol = ... Tls12` line below the first HTTPS call.
- **API drift (P2):** a route, method, header (`X-Api-Key`) or body field (`op`, `options`, `id`, `upload_url`, `download_url`, `seconds`) the client uses that `route()` / `run_job()` in `src/autoblender/api.py` doesn't produce or accept; an option shape other than one boolean checkbox per `/ops` option.
- **Limit drift (P2):** `$MaxJobs` must equal `api_concurrency` in `terraform/variables.tf`; `$http.Timeout` must exceed the `aws_lambda_function` `timeout` in `terraform/main.tf`. Quote both sides.

## Do NOT flag

- Style (aliases, casing, backtick continuations) unless it breaks 5.1.
- `.cmd` launcher flags that are documented 5.1 switches (`-NoProfile`, `-ExecutionPolicy Bypass`, `-STA`).
- Secrets handling — credentials-hygiene-reviewer's beat.
