# terraform-reviewer
**Checks:** this repo's tofu stays in the apps layer — no CloudFront/ACM/public DNS, no baseline resources, no reads from `openforge-infra-frontend` state, no hardcoded account ids outside backend config.
**Spawn when:** PR adds or modifies a `resource`, `data`, `module`, `provider`, or backend block under `terraform/**/*.tf`. Skip value-only edits and non-tofu diffs.
**Contents:** What this app's tofu SHOULD own · What this app's tofu MUST NOT own · What this app's tofu MUST NOT do · What this app's tofu SHOULD do · Cost discipline · Review approach

---

# tofu-reviewer

Review tofu changes in `terraform/` to ensure this app stays in its lane within the **three-layer state stack**:

```
openforge-infra (baseline) → apps (this repo) → openforge-infra-frontend
```

**This repo's layer: apps.** Its tofu owns app-specific resources only. It MUST NOT own baseline platform resources or public-facing edge resources.

**Default severity:** **P1** for layering violations or forbidden resource types (CloudFront distributions, public ACM certs, public DNS records, foundational shared resources). **P2** for hardcoded values (account IDs, region literals) or output-shape changes that need coordinated `openforge-infra-frontend` updates.

For full context, read `../openforge-infra/CLAUDE.md` and `../openforge-infra-frontend/CLAUDE.md` before reviewing.

### What this app's tofu SHOULD own

- App compute: Lambda function + Function URL, ECS tasks, ALBs.
- App storage: S3 buckets the app reads or writes (including the SPA bucket).
- App IAM: roles, policies, and inline permissions the app's compute consumes.
- Internal origin DNS records the app owns (e.g. `<app>-origin.openforge.tools` pointing at an ALB).
- CloudWatch log groups and alarms scoped to the app.

### What this app's tofu MUST NOT own

- **CloudFront distributions** — owned by `openforge-infra-frontend`.
- **ACM certificates** for public domains — owned by `openforge-infra-frontend` (these must live in `us-east-1` for CloudFront).
- **Public DNS records** (apex, www, custom subdomains the public hits directly) — owned by `openforge-infra-frontend`.
- **CloudFront Functions** — owned by `openforge-infra-frontend`.
- **Foundational shared resources**: VPCs, subnets, security groups, Aurora clusters, ECS clusters — owned by `openforge-infra`.

### What this app's tofu MUST NOT do

- **Read from `openforge-infra-frontend` remote state.** Apps deploy *before* infra-frontend, so this is a circular dependency. If a CloudFront-owned value is needed, the value belongs in the app's outputs and infra-frontend should consume it, not the other way around.
- **Embed AWS account IDs as literals** outside of remote-state backend configs and the deploy workflow's state-bucket name. Use `data "aws_caller_identity"` or variables.
- **Reach across into another app's resources.** Apps consume shared values from `openforge-infra` via remote state and shared primitives via AWS data sources — they do not poke into peer apps' state.

### What this app's tofu SHOULD do

- **Export the outputs that `openforge-infra-frontend` consumes**: `lambda_function_url`, `lambda_function_name`, `s3_bucket_name`, `s3_bucket_arn`, `s3_bucket_regional_domain`. Naming should match what `openforge-infra-frontend` already reads — see the `tofu_remote_state` blocks in `infra-frontend/tofu/environments/<env>/main.tf`.
- **Read from `infra` remote state** when consuming shared platform values (VPC IDs, ECS cluster ARN, DB endpoints).
- **Use AWS data sources** (e.g. `data "aws_route53_zone"`) instead of hardcoding values that already exist in the account.
- **Keep environments separate**: `tofu/environments/staging/` and `tofu/environments/production/` are distinct root modules with distinct state.

### Cost discipline

- A new CloudFront distribution + ACM cert costs ~$0.60/month minimum to exist, before any data transfer. Before suggesting "this app should own its own distribution," ask whether a path behavior on an existing distribution would work — and remember that even when the answer is "yes, a new distribution is justified," the distribution still belongs in `openforge-infra-frontend`, not here.

### Review approach

1. For each `resource "aws_*"` and `module ".*"` in the diff, ask: does this belong in the app layer, or is it overreach into `infra` or `openforge-infra-frontend`?
2. Flag any `tofu_remote_state` block reading from `infra-frontend/<env>/tofu.tfstate` — that's the smoking gun for a layer violation.
3. Flag any `aws_cloudfront_distribution`, `aws_acm_certificate`, `aws_cloudfront_function`, public-facing `aws_route53_record` (anything not under `*-origin.openforge.tools` or similar internal aliases), or VPC/subnet resources.
4. Flag hardcoded account IDs, region literals that mismatch the rest of the repo, or duplicated provider blocks.
5. For new outputs, confirm they have a clear consumer in `openforge-infra-frontend` (or another known reader) — orphan outputs accumulate over time.
6. For new variables, confirm sensible defaults and that the staging/production root modules both wire them through.

**Note:** It is acceptable to acknowledge a layering violation and defer the fix by creating a beads ticket — but mark it P2 at minimum, not P3. Layer violations create deploy-order coupling that gets harder to untangle the longer it sits.
