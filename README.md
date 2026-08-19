# SyncVey

English | **[日本語](README.ja.md)**

**One-stop AWS asset ledger with Terraform drift detection — self-hosted, no SaaS fees.**

SyncVey organizes your AWS resources into a **System → Environment → Asset** hierarchy,
flags configuration drift between your tfstate and live AWS state, and tracks application
metadata (language, framework, dependencies) per environment.
Spin it up with a single `docker compose up`.

> Live feature tour → **[https://mr-tabata.github.io/SyncVey/](https://mr-tabata.github.io/SyncVey/)**

---

## Screenshots

**Dashboard** — a hero-signal row answers what's happening (drift / EOL),
whether it's urgent (trend vs the last scan, freshness), and what to do next

![SyncVey Dashboard](docs/screenshots/dashboard-hero-en.png)

**Drift report** — attribute-level diff between tfstate and actual AWS state

![SyncVey Drift report](docs/screenshots/drift-en.png)

**Drift history** — drift trend over time, with the field-level diff for each snapshot

![SyncVey Drift history](docs/screenshots/drift-history-en.png)

![SyncVey Drift snapshot detail](docs/screenshots/drift-snapshot-en.png)

---

## Why SyncVey?

I'm 62, and I still write code. For years I watched infrastructure teams keep
their AWS inventory in spreadsheets — and every single time, without exception,
I watched those spreadsheets rot.

One day I asked the obvious question: we have tfstate, we have boto3 — why are we
still doing this by hand?

The other thing nagging at me was middleware EOL. We had a list of things to watch,
but no alerts, no dashboard, nothing that pointed to a next action —
just another spreadsheet quietly going stale.

So I built the tool I'd always wanted. Scheduled scans, drift detection, EOL alerts —
in one place, self-hosted, with your data staying inside your own infrastructure.

---

## How is SyncVey different?

Each tool in this space does one slice well. **driftctl** pioneered Terraform drift
detection, but it's a stateless CLI — and is no longer maintained since Snyk archived it.
**Steampipe** turns your cloud into queryable SQL, which is excellent for ad-hoc
investigation, but it's a query engine you build dashboards on, not a turnkey ledger.
**Cloud Custodian** shines at policy-as-code enforcement (tag, stop, remediate), yet it's
about reacting to rules, not giving you a browsable inventory. SyncVey sits in the middle
as a self-hosted **web app**: a persistent **asset ledger** with a UI, **attribute-level
drift** between your tfstate and live AWS — including resources created by hand in the
console that `terraform plan` never sees — plus a layer none of the others touch:
**per-environment application & middleware tracking with EOL alerts**. One
`docker compose up`, and your data stays in your own infrastructure.

| | tfstate drift | Detects console-made resources | Ledger + UI | App/middleware + EOL | Form factor |
|---|:---:|:---:|:---:|:---:|---|
| driftctl | ✅ | ✅ | ❌ | ❌ | CLI |
| Steampipe | ⚠️ DIY | ✅ (query) | ⚠️ build it | ❌ | CLI + SQL |
| Cloud Custodian | ❌ | ⚠️ policy-dependent | ❌ | ❌ | Policy engine |
| **SyncVey** | ✅ | ✅ | ✅ | ✅ | Self-hosted web app |

---

## Features

| Feature | Description |
|---------|-------------|
| **Asset ledger** | Inventory and search EC2, ECS, Lambda, RDS, DynamoDB, ElastiCache, EFS, EKS, S3, ALB, VPC, EBS, SNS, SQS, API Gateway, CloudFront, Route 53 resources — and more |
| **AWS scan** | Auto-discover 17+ resource types — compute, database, storage, network, messaging — in target accounts via AssumeRole |
| **Terraform integration** | Import assets by uploading a tfstate file |
| **Drift detection** | Spot attribute-level differences between tfstate and live AWS state |
| **Auto Scaling-aware drift** | Instances an Auto Scaling group launched or terminated are churn, not drift. SyncVey reads the `aws:autoscaling:groupName` tag it already scans — no extra API call or IAM — and keeps scale-out/in out of the drift count, shown transparently in their own section. Attribute changes on a persistent instance are still real drift. Toggle with `DRIFT_SUPPRESS_AUTOSCALING` |
| **Deleted-resource detection** | A resource that vanishes from AWS is flagged in the ledger and reported as *removed* drift — the row is kept, not deleted, so you can still see what was there and when it went. Marking only happens for the regions and resource types that scanned cleanly, so a throttled API call or an expired credential can never be mistaken for a mass deletion. If the resource comes back, the flag clears itself. Auto Scaling scale-in counts as churn, not drift |
| **Drift history** | Track drift over time — every scan/import records a snapshot, with a trend chart and per-snapshot diff |
| **Drift risk & attribution** | Grade drift by security impact (e.g. a security group opened to `0.0.0.0/0`) and trace *who* changed a resource via CloudTrail |
| **Drift briefing** | Optional weekly Slack rollup per system — severity counts, week-over-week trend, and the top risky changes with who made them (opt-in via `DRIFT_DIGEST_ENABLED`) |
| **Command line** *(optional plugin)* | Drive scan and drift from a terminal or CI with `manage.py syncvey scan / drift / status` — the same engine the dashboard uses. `drift --exit-code` fails the build on any drift; `--format json` feeds a pipeline. Detachable — remove the app and the command disappears |
| **Application tracking** | Record language, framework, deployment method, and dependencies per environment |
| **EOL alerts** | Flag end-of-life middleware/runtimes (offline by default; optional daily refresh) |
| **Architecture diagram** | Visualize resource relationships within an environment |
| **Multi-account** | Register an IAM Role ARN per system to manage multiple AWS accounts |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 / Django (server-rendered, no SPA) |
| Frontend | htmx 1.9 + Tailwind CSS — Django templates, no build step, no Node toolchain |
| Database | PostgreSQL 18.3 |
| AWS SDK | boto3 (cross-account access via AssumeRole) |
| Auth | TOTP two-factor authentication (pyotp) |
| Scheduler | django-apscheduler (periodic scans) |
| Infra | Docker Compose |

---

## Getting started

### Prerequisites

- Docker and Docker Compose

### 1. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Django
SECRET_KEY=your-secret-key
DEBUG=True

# Database (match docker-compose.yml)
DATABASE_URL=postgres://user:password@db:5432/asset_manager

# AWS — IAM user credentials for the central account
AWS_ACCOUNT_ID=123456789012
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_SCAN_REGIONS=ap-northeast-1,ap-northeast-3
```

> **Production note:** set `DEBUG=False` for any non-local deployment, and use a strong, unique `SECRET_KEY`.

### 2. Start the app

```bash
docker compose up -d
```

> **Tip:** If you use VS Code, open the repo in Dev Containers — the environment is built automatically.

### 3. Apply migrations and (optionally) seed sample data

```bash
docker compose exec app python manage.py migrate
docker compose exec app python manage.py seed   # optional: loads sample data
```

### 4. Open in your browser

| Service | URL |
|---------|-----|
| App | http://localhost:8000/ |
| Django Admin | http://localhost:8000/admin/ |

---

## Setting up AWS scan

Create a read-only IAM Role in each target AWS account and register its ARN in SyncVey.

Full instructions → [aws-setup.md](aws-setup.md)

**Quick overview:**
1. Add your central account's IAM credentials to `.env`
2. Create the `SyncVeyReadOnly` role in each target account using the provided IAM policy ([`iam/iam-policy.json`](iam/iam-policy.json))
3. Register the Role ARN using the 🛡 button on the system card
4. Click **ScanLine** to run the first scan

---

## External network calls

SyncVey is fully self-hosted. The only outbound connections it makes are listed below —
there is no telemetry or usage analytics.

| Destination | When | Direction | How to control |
|-------------|------|-----------|----------------|
| AWS APIs (boto3 / AssumeRole) | On-demand or scheduled scan | Outbound HTTPS | Only when a Role ARN is configured |
| AWS CloudTrail (`LookupEvents`) | Clicking "Who changed this?", or the weekly drift briefing | Outbound HTTPS | Lazy, never automatic — needs the role + `cloudtrail:LookupEvents` |
| `hooks.slack.com` | Drift detected, or the weekly drift briefing | Outbound HTTPS | Per-system Slack Webhook URL (opt-in); briefing also needs `DRIFT_DIGEST_ENABLED=true` |
| `endoflife.date` | Daily EOL data refresh | Outbound HTTPS | **Off by default** — enable with `EOL_REFRESH_ENABLED=true` |

**EOL refresh detail.** End-of-life detection works offline using built-in data.
Set `EOL_REFRESH_ENABLED=true` to enable a daily job that pulls fresh data from
[endoflife.date](https://endoflife.date/) (falls back to built-in data on failure).
By default, only dependencies you actually track are refreshed; set
`EOL_REFRESH_DYNAMIC=false` to pin it to a fixed known set.
You can also trigger a refresh manually:

```bash
docker compose exec app python manage.py refresh_eol --force
```

---

## Routes

SyncVey is a server-rendered htmx app, not a JSON REST API.
All views return rendered HTML (full pages or partials).

```
/                                      Dashboard
/systems/                              Systems
/systems/<id>/environments/            Environments under a system
/systems/<id>/applications/            Applications under a system
/environments/<id>/scan/               Run an AWS scan
/environments/<id>/drift/              Drift report
/environments/<id>/drift/history/      Drift history (trend over time)
/drift-risk/                           Drift risk & attribution (security triage)
/drift-digest/                         Drift briefing (weekly Slack rollup preview)
/environments/<id>/diagram/            Architecture diagram
/environments/<id>/sync-s3/            Sync remote tfstate from S3
/assets/                               Asset list
/assets/<id>/                          Asset detail
/upload-tfstate/                       Import assets from tfstate
/samples/                              Sample library
/audit-log/                            Audit log
/profile/                              Profile / 2FA settings
/admin/                                Django Admin
```

Full route list: [asset_manager/urls.py](asset_manager/urls.py)

---

## Command line *(optional plugin)*

The `syncvey_cli` plugin adds a `syncvey` management command so an operator — or
a CI pipeline — can trigger a scan and read drift without opening the web UI. It
drives the same scan/drift engine the dashboard does, so it can never disagree
about what counts as drift.

```bash
# Run a live AWS scan and record a drift snapshot (all systems, or one)
docker compose exec app python manage.py syncvey scan --system e-commerce

# Print the current drift; add --format json to feed a pipeline
docker compose exec app python manage.py syncvey drift --env prod

# Fail the build on any drift — drop this into a CI step
docker compose exec app python manage.py syncvey drift --exit-code

# List systems / environments with asset counts and last-scan time
docker compose exec app python manage.py syncvey status
```

Exit codes are the CI contract: `0` = ok / no drift, `1` = drift found
(only with `drift --exit-code`), `2` = a scan job failed or the selector
matched nothing. Detachable like the other plugins — remove
`syncvey_cli` from `INSTALLED_APPS` and the command disappears.

---

## Data model

```
System
  └── Environment  (PROD / STG / DEV / QA)
        └── Asset  (asset_type + category; raw attributes stored in a JSON field)
  └── Application
        └── AppEnvConfig  (per-environment settings)
              └── AppDependency
```

---

## Development

```bash
# Open a shell inside the app container
docker compose exec app bash

# Generate migrations after model changes
docker compose exec app python manage.py makemigrations

# Tail logs
docker compose logs -f app
docker compose logs -f db
```

---

## Project

| | |
| --- | --- |
| Releases and changes | [CHANGELOG.md](CHANGELOG.md) |
| Reporting a vulnerability | [SECURITY.md](SECURITY.md) — please don't use a public issue |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |

---

## License

[MIT](LICENSE)
