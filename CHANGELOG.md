# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, minor releases may change behaviour.

## [Unreleased]

## [0.9.1] — 2026-10-23

Internal code health, security hardening, and branding consistency updates.

### Fixed

- **TOTP application issuer name**: Updated the 2FA authenticator issuer name from legacy `Cloud Asset Manager` to `SyncVey` (`views.py`).
- **Membership role update authorization**: Hardened `membership_update_view` to enforce organization scope lookup, returning 404 rather than 403 on cross-organization member IDs to prevent ID existence discovery.
- **Vulnerability scan loop variable binding**: Explicitly bound loop variables `(eco, name, version)` in vulnerability record builder lambda to prevent potential closure rebinding issues (`vulns.py`).
- **Cleaned up imports and dead code**: Removed unused imports across `views.py`, `apps.py`, `repo_import.py`, and `scheduler.py`, and dropped unused topology local variable `others`.

## [0.9.0] — 2026-10-13

The libraries a system uses now follow its GitHub repository on their own, every day — nothing to upload by hand — and known vulnerabilities cover ElastiCache engines too. This release adds migration `0022` (applied by `migrate`, as in the setup steps).

### Added

- **Lockfiles now follow the application's GitHub repository, every day.**
  A lockfile uploaded by hand is stale by tomorrow — the opposite of why
  SyncVey exists — so the way in is now a repository. Under an environment,
  *Register and import* takes a repository URL (or `/tree/<branch>/<folder>`
  for a monorepo, e.g. a different branch per environment) and reads the
  lockfiles at the top level — `requirements.txt`, `package-lock.json`,
  `composer.lock`, `Gemfile.lock`, `go.mod` — through GitHub's API. From then
  on a daily job (03:30; `manage.py refresh_repos --force`; off with
  `REPO_REFRESH_ENABLED=false`) re-imports them, so the library list and its
  vulnerabilities follow the repository without anyone touching it. The screen
  shows the last sync; a failed sync is shown as such and the last good list is
  kept. Nothing is called until a repository is registered, and only
  `api.github.com` is ever called: the URL is split into owner / repository /
  folder and never fetched as typed. Private repositories need a read-only
  token in `SYNCVEY_GITHUB_TOKEN` (read from the environment, never stored or
  logged). Importing one file by hand remains as a folded-away fallback and says
  it goes stale. Adds migration `0022`.
- **Known vulnerabilities for ElastiCache Redis and Memcached.** The same
  `VULN_REFRESH_ENABLED=true` job now looks ElastiCache engine versions up in
  NVD, with the same badge, CVE list and "AWS may have already fixed some of
  these" note as RDS. Only versions that carry a patch number (`5.0.6`,
  `1.6.6`) are checked: ElastiCache updates `7.1`-style versions behind the
  scenes, so matching those would report CVEs that are already fixed.
  Valkey isn't in NVD and is skipped.

### Changed

- **Importing a lockfile is now one button.** The browser's own file control
  ("Choose File / No file chosen", plus a tooltip) is gone: press *Import
  lockfile*, pick a file, and it is imported right away — no second click, and
  nothing left half-done after choosing. The hint under it says so.
- **The resource list and detail no longer say "EKS supports Kubernetes 1.34
  and later".** A past or nearing end-of-life version now shows just the
  fact — the version, the stages and the dates. Whether to update is left to
  you.
- **CVE descriptions from NVD are shown as the first sentence** (or cut at a
  word with "…"), instead of a 300-character cut that could end mid-word.

## [0.8.0] — 2026-10-13

End-of-life is now judged on AWS's own support schedule, known vulnerabilities appear next to it — for your libraries (from lockfiles) and for RDS engines — and the dashboard's EOL card links to what it counted. This release adds migration `0021` (applied by `migrate`, as in the setup steps).

### Added

- **Known vulnerabilities for your libraries.** On *Applications & Middleware*,
  each environment can *Import lockfile* (`requirements.txt`,
  `package-lock.json`, `composer.lock`, `Gemfile.lock`, `go.mod`): the file is
  parsed in memory, the libraries are recorded, and the file is discarded.
  With `VULN_REFRESH_ENABLED=true` (off by default) a daily job — or
  `manage.py refresh_vulns --force` — looks them up on OSV.dev and flags those
  also in CISA's Known Exploited Vulnerabilities catalog. Each vulnerable
  library lists its advisories with severity, fixed versions and a link; the
  summary shows how many are high/critical or known exploited. Only
  (ecosystem, package, version) is sent. Hand-entered libraries are not
  checked, and an OSV outage keeps the previous result rather than showing
  "no vulnerabilities". Dependencies are now unique per version, so two
  copies of one package (nested `node_modules`) are both kept.
- **Known vulnerabilities for RDS PostgreSQL and MySQL engines.** The same
  `VULN_REFRESH_ENABLED=true` job looks each engine version up in NVD (only the
  product and version are sent; an optional `NVD_API_KEY` lifts NVD's rate
  limit, otherwise it takes about 7 seconds per version). An instance shows a
  badge in the resource list and a collapsible CVE list in its detail panel,
  with severity, a link to NVD and a "known exploited" mark. The result is for
  the upstream version, and the panel says AWS may already have fixed some of
  it inside RDS. Aurora is skipped, since its versions don't match upstream.
  The OSV and NVD lookups are independent: one being down keeps the other's
  results and the previous records.
- **More plugin seams, so a separately distributed plugin app can add a
  sign-in option or routes without the core importing it:** extra apps can be
  installed with `SYNCVEY_EXTRA_APPS`, and an `AppConfig` can contribute
  `login_buttons(request)` (shown on the sign-in page) and `urls_module`.

### Changed

- **RDS, Lambda and EKS are now judged against AWS's own support schedule,
  in stages, once EOL data has been fetched** (`EOL_REFRESH_ENABLED=true`).
  What matters operationally is the day AWS stops supporting a version, not
  the upstream project's. An RDS PostgreSQL/MySQL version or EKS cluster
  past standard support is shown as **extended support (paid)** and counted
  as nearing end-of-life; it is *past end-of-life* only once extended
  support ends too. A Lambda runtime past its deprecation date is shown as
  **deprecated**, and *past end-of-life* once AWS blocks updates. The
  asset's detail panel lists both dates and names its basis. Aurora keeps
  the upstream approximation (it has its own schedule), as does anything
  not covered by the fetched data, and offline installs behave as before.
  For extended support the panel says it carries an additional charge and
  links to AWS's pricing page (amounts change, so none are shown); for a
  deprecated Lambda runtime it links to AWS's runtime support policy.

### Fixed

- **The dashboard's end-of-life card now links to what it counted.** Since
  0.7.0 it counts RDS / Lambda / EKS resources as well as declared
  dependencies, but "Review runtimes" always opened the (often empty)
  applications screen. It now offers "Review resources (N)" — the asset list
  filtered to resources past or nearing end-of-life (`?eol=1`, with a banner
  and a way back) — and "Review dependencies (N)", each shown only when it
  has something to open.

## [0.7.0] — 2026-10-08

End-of-life is now judged for RDS, Lambda and EKS resources too, the ledger can be exported, and CI finally runs the plugins' tests.

### Added

- **End-of-life is now judged for the resources themselves, not only for
  the dependencies an application declares.** An RDS instance's engine and
  version (PostgreSQL / MySQL, including Aurora), a Lambda function's
  runtime (Python / Node.js / Ruby / Java) and an EKS cluster's Kubernetes
  version are checked against the same EOL data. Resources past EOL or
  nearing it are counted on the dashboard's EOL card and badged in the asset
  list. The badge sits on the line that shows the version it is about — an
  icon for the kind (runtime / database engine / Kubernetes version), the stored
  value (`python3.7`, `mysql 5.7.44`, `Kubernetes 1.24`), then the badge — not
  next to the service name, so it does not read as the service itself being
  retired. Vanished resources (`missing_since`) are not counted. Aurora and
  Lambda are judged against the upstream engine / language dates, which are
  close to but not the same as AWS's own deprecation dates; EKS has no built-in
  dates and is judged only once the EOL data has been fetched
  (`EOL_REFRESH_ENABLED=true`).
- **`manage.py syncvey export` writes the asset ledger as JSON or CSV**
  (`--format`, `--system`, `--env`, `--output FILE`). Every asset is included —
  vanished ones too, with `missing_since` set — together with its EOL verdict;
  JSON also carries each asset's stored attributes (`--no-raw` leaves them out).
- **Issue forms (bug report, feature request) and a pull request template.**
  Security reports are pointed to the private advisory form.

### Fixed

- **The asset list never showed an RDS instance's engine.** The row printed the
  instance class and stopped, so `postgres 16.2` was hidden behind
  `db.t3.micro`. Both are shown now.
- **The asset detail window showed no attributes at all.** After the per-type
  detail tables were removed, `asset_detail_view` stopped passing the data the
  detail partials render, so the window held only the name, region and
  timestamps. It now lists the stored attributes (`raw_data`, with internal and
  empty keys left out) and, for RDS / Lambda / EKS, an end-of-life panel with
  the cycle's support-end date, headed "Middleware support" and saying whether
  the version is a runtime, a database engine or a Kubernetes version.
- **The asset cards were all different heights.** The grid top-aligned each card
  at the height of its own content, so an RDS card (instance class, engine,
  badge) towered over a Lambda one. Every card now takes the height of the
  tallest.
- **The detail window's icon was broken and its provider / type pills were empty.**
  The icon pointed at `static/aws-icons/`, which does not exist (the list uses
  `static/cloud-icons/aws/`), and the pills called `get_provider_display` /
  `get_asset_type_display`, which Django only generates for fields with
  `choices` — these two have none, so the template printed nothing. The window
  now uses the same icon lookup as the list and prints the provider and type.
- **Uploading an encrypted OpenTofu state ended with "0 asset(s) registered".**
  OpenTofu 1.7+ can encrypt state; the file then holds only ciphertext and no
  `resources`, which looked like a successful, empty import. It is now refused
  with a message saying the file has to be decrypted first.
- **The plugins' tests were never run by CI.** `syncvey_cli` and
  `syncvey_drift_risk` keep their tests in Django-style `tests.py`, which
  `pytest.ini` did not collect, so CI silently skipped 56 tests (the workflow
  even had a comment saying plugin tests must not slip out). `pytest.ini` now
  collects `tests.py` too.

### Verified

- OpenTofu state files (provider `registry.opentofu.org/...`) import exactly
  like Terraform's. Checked with a state in OpenTofu's documented format, not
  with the output of a real `tofu` run.

## [0.6.0] — 2026-09-16

Four missing IAM permissions were making four scanners fail silently.

### Fixed

- **The distributed IAM policy was missing 4 permissions, so EBS, EFS, SNS,
  and SQS silently dropped out of the asset ledger.** `iam/iam-policy.json`
  lacked `ec2:DescribeVolumes`, `elasticfilesystem:DescribeFileSystems`,
  `sns:ListTopics`, and `sqs:ListQueues` (plus `sqs:GetQueueAttributes`,
  needed for queue detail), so a role set up exactly as `aws-setup.md`
  describes hit an `AccessDenied` on those 4 of 18 scanners. Because a failed
  scanner is reported and skipped rather than raising, the gap never
  surfaced as an error — the resources were just absent. Confirmed against a
  real AWS account on 2026-09-07. Added new `SyncVeyMessagingReadOnly` /
  extended `SyncVeyStorageReadOnly` statements for the 4 actions.

### Changed

- **The dashboard's three signal cards (drift / EOL / freshness) now share
  one skeleton, and speak in numbers even when there's nothing to report.**
  They used to be two different HTML blocks per card — an "alert" version
  and an "all clear" version with different copy — so a calm-day screenshot
  and a noisy-day screenshot looked like different products. Every card now
  always shows label → number → one line of context → an action, and
  "nothing wrong" renders as `0`, not a sentence, so the eye doesn't have to
  re-read the tile to tell whether something changed. The drift trend chart
  on the environment history page also grew a breakdown: each bar is now
  stacked into changed/added/removed instead of one color, matching the
  legend already used in the snapshot list below it.
- **Opening an htmx partial URL directly (reload, bookmark, a pasted link)
  no longer shows a bare, unstyled fragment.** Most screens in this app are
  `#main-content` swaps that return HTML with no `<head>`, so navigating to
  one of those URLs directly skipped the `<head>` that loads Tailwind — the
  page rendered with classes present but no CSS applied. A new
  `ShellFallbackMiddleware` detects a real browser navigation
  (`Sec-Fetch-Mode: navigate`, not an htmx/fetch request) returning a bare
  fragment, and wraps it in the same shell the normal dashboard uses, so
  every entry point lands on the same screen.

## [0.5.0] — 2026-09-04

Times now say which timezone they are in.


### Changed

- **Times are shown in your timezone, and say which one.** `TIME_ZONE` was
  pinned to `UTC` and nothing on screen said so, so a drift snapshot taken at
  09:10 in Tokyo read as `2026-09-04 00:10` — the right instant, displayed in a
  way that looks like the wrong one. Drift detection is about *when* something
  changed, and these timestamps get lined up against the AWS console and
  CloudTrail; an unlabelled nine-hour gap is a misreading waiting to happen.

  `docker compose` now passes the host's `TZ` through, so
  `TZ=Asia/Tokyo docker compose up` shows local time, and every absolute
  timestamp carries its zone: `2026-09-04 09:10 JST`. It still defaults to UTC
  when `TZ` is unset — unambiguous beats guessing.

  **Stored data is untouched.** Everything is still kept in UTC (`USE_TZ` was
  already on); only the display changed. A test walks the templates and fails if
  a new time-of-day format ships without its zone.


## [0.4.0] — 2026-08-27

Starting the app is now a pull, not a build.

### Changed

- **`docker compose up -d` starts from the published image instead of building
  one.** It used to run apt and pip on the user's machine — several minutes
  before the first screen, on a laptop that has no reason to compile anything.
  Compose now points at `jiniie/syncvey:latest` (amd64 and arm64, published on
  `v*` tags), so starting the app is a pull. `SYNCVEY_IMAGE` in `.env` pins a
  version. It also gives the project its first countable install: a `git clone`
  is not measured by anything, and release Source code archives are not either
- **Building from source became the developer path**, where it belongs.
  `cp docker-compose.override.yml.example docker-compose.override.yml` gives
  `build: .` plus the working-tree mount — the old behaviour with live reload.
  README (both languages) and the landing page say which of the two you get (#34)

### Documentation

- The development-history page explains why its first two weeks carry no pull
  requests: this repository was re-created on 17 June, and the work before that
  survives as commits while the pull requests went with the old repository.
  Elapsed days and the weekly chart count from the first commit in git
- That page had been reachable only from the landing page's own navigation.
  It now has an entry from the README (both languages) and from the download
  page, and the site has a `sitemap.xml` and `robots.txt` (#36)

## [0.3.0] — 2026-08-23

Makes the project something you can try without an AWS account, and something
whose adoption can actually be measured. Also fixes a drift report that told
the truth on screen and the opposite on the command line.

### Changed

- **One place decides what drift is.** The classification — appeared,
  disappeared, changed, Auto Scaling churn, and the order those are judged in —
  was hand-written in four callers: the environment badge, the snapshot writer,
  the drift report and the CLI. Every time the core learned a category, some
  copies kept the old answer: `removed` shipped in 0.1.0, two copies caught up
  in #24, and the fourth was still calling a deleted resource an addition until
  the fix earlier in this release. The rule now lives in `asset_manager/drift.py`
  as `classify()`; the four callers ask it and keep only their own output shape
  (counts, JSON, template rows). No behaviour change — a new test asserts all
  four report identical numbers for the same environment, so a fifth copy is a
  failing build rather than a bug report

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
- Published Docker images — `v*` tags build `jiniie/syncvey` for amd64 and
  arm64 and push it to Docker Hub. Until now the only install path was a
  `git clone`, which nothing counts: release Source code archives are not
  measured either, so "how many downloads?" had no answer that could exist.
  A pull request that touches the Dockerfile builds the image without pushing,
  so a broken build surfaces on the branch instead of on release day (#33)
- Metrics collection — `scripts/metrics.py report` prints Docker pulls, release
  asset downloads and GitHub traffic in one place, and says which figures do
  not exist yet rather than printing a confident zero. A scheduled job stores
  the traffic numbers daily in a private Gist, because GitHub keeps only the
  last 14 days and a day not collected is gone for good (#33)

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

[Unreleased]: https://github.com/MR-TABATA/SyncVey/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/MR-TABATA/SyncVey/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MR-TABATA/SyncVey/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MR-TABATA/SyncVey/releases/tag/v0.1.0
