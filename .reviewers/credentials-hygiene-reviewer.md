# credentials-hygiene-reviewer

Review PRs for **secrets and credential handling**. This repo holds a Google OAuth client (client id/secret + a long-lived Drive refresh token) and deploys to AWS. Credentials live in Secrets Manager (written by `bin/drive-auth`) or the environment and must never enter the repo, the image, its logs, or its test data.

**What to flag:**

1. **Literal secrets in code or config (P1):** any token, API key, JWT (`eyJ...`), password, client secret, or connection string with embedded credentials committed in source, JSON/YAML fixtures, test files, or docs. Includes "temporarily for testing" — a committed secret is compromised regardless of intent and requires rotation, not just removal (git history preserves it).

2. **OAuth artifacts committed (P1):** `client_secret*.json`, token dumps, or Secrets Manager payloads in the diff. Check `.gitignore` covers the pattern; check `.dockerignore` keeps them out of the image.

3. **Secrets in URLs (P2):** tokens as query parameters (`?access_token=...`, `?token=...`) in code when a header alternative exists. URLs land in server logs, browser history, and proxies. Runtime-only exceptions (an API that *requires* a URL token) must confine the URL construction to one place and never log the assembled URL.

4. **Secrets echoed to output (P1):** tokens in exception messages, `print()` diagnostics, CLI output, or assertion messages. (Log-call interpolation is `logging-reviewer`'s beat — this covers the non-logging leak paths.) Also flag debug endpoints or CLI flags that dump full config including credentials.

5. **Insecure storage of tokens the tool persists (P2):** `bin/drive-auth` persists the Drive refresh token. Flag: tokens written into the repo tree, into fixture files, into world-readable paths, or into files not covered by `.gitignore`. Acceptable: env files outside the repo, `~/.config`-style dotfiles with `0600`-style expectations, OS keyring.

6. **Test fixtures with realistic-looking credentials (P3):** tests should use obviously-fake values (`"test-token"`, `"fake-client-id"`), not plausible or expired-real ones. An expired-real JWT in a test still reveals account ids, scopes, and endpoint shapes.

7. **New env var credentials without documentation (P3):** a new required credential env var should be named in the relevant doc/README section (name only — never the value) so setup doesn't require reading source.

**Do NOT flag:**

- Reading credentials from `os.environ` / env files — that's the correct pattern.
- Public identifiers that aren't secrets (Drive folder ids, AWS account ids, bucket names, ARNs).
- Example placeholders in docs (`<your-token-here>`, `sk-xxxx...`).
- The word "token"/"secret" in variable names, comments, or docs — the *values* are the concern.

**Review approach:**

1. Scan added lines for high-entropy strings, `eyJ`-prefixed blobs, `Bearer <literal>`, `client_secret=<literal>`, connection strings with passwords.
2. Check the diff file list for credential-file-shaped additions; verify `.gitignore` coverage for new artifact patterns the PR's tooling produces.
3. For code that builds authenticated requests: header vs URL token placement; is the assembled URL ever logged/printed?
4. For token persistence code: where does it write, and is that location inside the repo or gitignored?
5. For tests/fixtures touching auth: are the values obviously fake?
