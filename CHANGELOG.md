# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, minor releases may change behaviour.

## [Unreleased]

### Added

- Try it without an AWS account — a `demo` Compose profile starts a local AWS
  emulator (LocalStack), `scripts/seed_localstack.py` fills it with fake
  resources, and the normal scan/drift path runs against it. No account, no
  credentials, no bill. Ten of the eighteen scanners work on the emulator's
  free edition; the other eight report an error per service and the scan
  carries on, which is exactly the behaviour deleted-resource detection relies
  on. The image tag is pinned to `localstack/localstack:4` because `:latest`
  now requires an auth token and exits without one. README documents what does
  and does not work, CloudTrail attribution included

### Fixed

- **The CLI reported a deleted resource as an addition.** `syncvey drift`
  classified assets on `raw_data_prev` alone, and a resource that disappears
  from AWS never gets one — so a deletion printed as `+ added`, the exact
  opposite of what happened. Same class as the 0.2.0 fix: the core grew a
  `removed` category and a hand-written copy of the classification was left
  behind, this time in the CLI plugin. It now branches on `missing_since`
  first, like `_record_drift_snapshot` and the drift report do, and an
  ASG-owned disappearance stays churn rather than failing a build. Found by
  running the scanner against the emulator above

## [0.2.0] — 2026-08-20

Adds blast-radius impact analysis, closes a drift-counting bug that hid deleted
resources, and puts both halves of the translation problem behind CI gates.

### Added

- Blast radius *(plugin)* — walk the resource reference graph outward from each
  drift and rank everything it reaches by severity-weighted, distance-decayed
  impact. Recovers the dependency graph from scanned attribute values, scoped
  per environment so identical IDs in different environments never wire
  together. Detachable like the other plugins (#15)
- CI gate for unreviewed translations — `makemessages` never leaves a new
  string blank; it copies the nearest existing translation and marks the guess
  `#, fuzzy`. From there the string either silently falls back to English or
  ships visibly wrong, and neither is visible to a reviewer reading the diff in
  the source language. The gate fails the build on fuzzy or empty entries (#13)
- CI gate for strings that were never extracted — the gate above can only judge
  entries that are *in* the catalogue. A string wrapped in `{% trans %}` that
  `makemessages` was never run against is absent entirely, so nothing flags it
  and it renders in English. This one runs `makemessages` against a throwaway
  copy and compares msgid sets. Both of this repo's translation holes (50
  strings, then 15) were found by a human noticing English on a Japanese
  screen; this catches them on the branch that introduces them (#26)

### Fixed

- **Deleted resources were missing from the drift totals.** The `removed`
  category added in 0.1.0 updated `DriftSnapshot.total_count`, but two places
  built the total by hand and were never updated: the dashboard hero band
  reported "no drift detected" for an environment where resources had been
  deleted, and the weekly Slack briefing under-counted both the total and the
  week-over-week trend. Both exist to make deletions noticeable, so silently
  dropping them was the worst possible failure (#24)
- 50 translatable strings had never been extracted into the Japanese catalogue
  and rendered in English inside the Japanese UI — 21 drift-risk templates,
  13 drift-risk Python strings, and 17 in the dashboard hero band. All reviewed
  and translated by hand (#14)
- 15 more never-extracted strings, found while taking screenshots: the whole
  blast-radius screen, the Auto Scaling section of the drift report, and the
  `Missing Since` field. Extraction produced three fuzzy guesses that were all
  wrong — `Auto Scaling` had become `自動スキャン` ("Auto Scan") — which is
  exactly the failure the fuzzy gate exists for (#25)

### Infrastructure

- Folded the standalone i18n workflow into the main CI workflow. It triggered
  on every push to every branch with no concurrency group, so a single pull
  request ran it eight times while CI ran once (#23)

## [0.1.0] — 2026-08-19

First tagged release. SyncVey has been usable for a while; this marks the point
where the surface is stable enough to pin a version to.

### Added

**Ledger and discovery**

- Asset ledger across 17+ AWS resource types — EC2, ECS, Lambda, RDS, DynamoDB,
  ElastiCache, EFS, EKS, S3, ALB, VPC, EBS, SNS, SQS, API Gateway, CloudFront,
  Route 53 and Secrets Manager
- Live AWS scan via boto3, cross-account through AssumeRole, read-only by design
- Terraform integration — import assets by uploading a `tfstate` file, with a
  warning before import when the file carries sensitive values
- Scheduled scans

**Drift**

- Drift detection — attribute-level diff between tfstate and live AWS state
- Drift history — every scan or import records a snapshot, with a trend chart
  and a per-snapshot diff, capped per environment by `DRIFT_SNAPSHOT_RETENTION`
  (#2)
- Deleted-resource detection — resources that vanish from AWS are flagged and
  reported as *removed* drift; rows are kept rather than deleted, and marking is
  confined to the regions and resource types that scanned cleanly so a transient
  API error can never be mistaken for a mass deletion (#21)
- Auto Scaling-aware drift — instances an Auto Scaling group launches or
  terminates count as churn, not drift, read from the
  `aws:autoscaling:groupName` tag with no extra API call or IAM permission;
  toggle with `DRIFT_SUPPRESS_AUTOSCALING` (#17)
- Drift risk and attribution *(plugin)* — grade drift by security impact and
  trace who changed a resource via CloudTrail (#7)
- Secret rotation drift *(plugin)* — flag Secrets Manager secrets whose
  rotation should have happened but didn't, a standing-state check rather than a
  diff (#12)
- Weekly drift briefing *(plugin)* — opt-in Slack rollup per system, behind
  `DRIFT_DIGEST_ENABLED` (#9)

**Applications and lifecycle**

- Application tracking — language, framework, deployment method and
  dependencies per environment
- EOL alerts for end-of-life middleware and runtimes, offline by default with an
  optional daily refresh from `endoflife.date`

**Interface**

- Dashboard with a hero-signal row for drift trend, EOL and scan freshness (#6)
- Architecture diagram of resource relationships within an environment
- Command line *(plugin)* — `manage.py syncvey scan / drift / status`, driving
  the same engine as the dashboard; `drift --exit-code` fails a build on drift
  and `--format json` feeds a pipeline (#16)
- Sample library — importable example tfstate files for trying the app without
  an AWS account
- Japanese and English UI

**Platform**

- Multi-tenancy with per-organization isolation
- TOTP two-factor authentication and an audit log
- Feature flags and a detachable-plugin seam: optional apps are discovered at
  runtime, and removing one hides its navigation and 404s its routes (#1)
- Docker Compose deployment

### Security

- Read-only IAM policy shipped in [`iam/iam-policy.json`](iam/iam-policy.json)
- Startup refuses an unset `SECRET_KEY` when `DEBUG=False`; session and CSRF
  cookies default to `Secure` outside debug
- Uploaded tfstate files are scanned for sensitive values, with an explicit
  confirmation step before import
- No telemetry. Outbound connections are limited to AWS unless an operator opts
  in to Slack, EOL refresh or CloudTrail attribution — all documented in the
  README

### Infrastructure

- Test suite on GitHub Actions — unit and integration on PostgreSQL across
  Python 3.12 and 3.13, Playwright E2E, and a documentation consistency check
  (#20)
- Configuration-driven documentation consistency checker (#19)

[Unreleased]: https://github.com/MR-TABATA/SyncVey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MR-TABATA/SyncVey/releases/tag/v0.1.0
