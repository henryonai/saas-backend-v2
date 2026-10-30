# Security

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately on GitHub
instead: the repository's **Security** tab → **Report a vulnerability**. Only the maintainer
sees the report.

Include what you found, where (file and line, or the request), how to reproduce it, and what an
attacker could do with it. You'll get an answer within a week. Once it is fixed, the advisory is
published with credit to you, unless you'd rather stay anonymous.

## What counts

Anything in this repository that lets someone get past what the README's "Security model"
promises, for example:

- reading or changing another workspace's data (tenant isolation, the `X-Tenant-Id` check);
- acting with a role the ID token doesn't give (`get_current_user`, `layers/shared/layer/shared/auth.py`);
- forging or replaying a Stripe webhook, or changing a plan without paying;
- an IAM permission in `template.yaml` or `.github/deploy-role.yaml` that is broader than it needs to be;
- a secret that ends up in logs, an event, an export or the browser.

## What doesn't

- A stack you deployed and then configured differently from the README (for example without the
  JWT authorizer, or with your own IAM changes).
- Problems in AWS, Stripe, Cognito or a dependency itself: report those to them. A dependency
  this repository should update is welcome as an ordinary issue.

## Supported versions

Fixes go to `main` and the next release. Copies made from the template don't update themselves:
watch the repository's releases and advisories, and merge the fixes into your copy.
