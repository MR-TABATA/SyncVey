# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, minor releases may change behaviour.

## [Unreleased]

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

[Unreleased]: https://github.com/MR-TABATA/SyncVey/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MR-TABATA/SyncVey/releases/tag/v0.1.0
