# Security Policy

日本語版は [SECURITY.ja.md](SECURITY.ja.md) にあります。

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it through GitHub's private vulnerability reporting instead:

> **[Report a vulnerability](https://github.com/MR-TABATA/SyncVey/security/advisories/new)**

(Repository page → **Security** tab → **Report a vulnerability**)

That channel is private between you and the maintainer, so the details stay out
of public view until a fix is available.

Please include:

- what the problem is, and what an attacker gains from it
- the steps to reproduce it (a minimal case is worth more than a long one)
- the version or commit you saw it on
- any workaround you already know about

This is a solo-maintained project, so response times are best-effort rather
than contractual. Expect a first reply within about a week. If a report goes
unanswered for longer than that, feel free to ping the advisory thread.

## What this project holds

SyncVey is self-hosted, which means **the operator holds the data, not the
project**. There is no hosted service and no telemetry — nothing is sent
anywhere by default. What a deployment does hold is worth knowing when you
assess a finding:

| Held | Why it matters |
| --- | --- |
| AWS credentials or a role ARN | Read-only by design (see [`iam/iam-policy.json`](iam/iam-policy.json)), but they reach a real AWS account |
| Scanned resource metadata | Instance IDs, ARNs, endpoints, tags — an inventory of the operator's infrastructure |
| Imported tfstate | Uploaded files are scanned for sensitive values and the operator is warned before import |
| User accounts and TOTP secrets | Authentication material for the dashboard |

Findings that expose any of the above — especially cross-tenant access, since
the app is multi-tenant — are the ones worth reporting first.

## Outbound connections

By default SyncVey talks only to AWS. Everything else is opt-in and documented
in the README's "outbound connections" table (Slack webhooks, `endoflife.date`
refresh, CloudTrail attribution). A build that connects somewhere not on that
list is itself a bug worth reporting.

## Deploying it safely

The defaults assume a real deployment, not a demo:

- `SECRET_KEY` must be set when `DEBUG=False` — the app refuses to start otherwise
- Session and CSRF cookies default to `Secure` when `DEBUG=False`
- `ALLOWED_HOSTS` must be set explicitly in production

`DEBUG=True` is for local development only. Running it on a reachable host
exposes stack traces and settings.

## Supported versions

Only the latest release receives fixes. There are no maintained release
branches.
