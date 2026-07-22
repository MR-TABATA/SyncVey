"""
service.py
----------
The reusable logic behind the `syncvey` management command, kept out of the
command class so it can be unit-tested without argparse in the way.

Everything here imports the *core* scan/drift engine (a plugin may depend on
the core; the core never depends on a plugin). In particular the drift
computation is the core's own `_compute_raw_diff` / `_record_drift_snapshot`,
so the CLI's answer to "what drifted?" is byte-for-byte the dashboard's — the
subtle intersection-diff rule (scan emits ~11 keys, tfstate 50+, so only shared
keys are compared) lives in one place and the CLI inherits it.
"""

from django.db.models import Count, Q
from django.utils import timezone


def resolve_systems(system_selector=None):
    """
    Systems to act on. `system_selector` matches a System by `code` or `name`
    (exact); None means every system. Returns a list (empty if nothing matched).
    """
    from asset_manager.models import System

    qs = System.objects.all().order_by('name')
    if system_selector:
        qs = qs.filter(Q(code=system_selector) | Q(name=system_selector))
    return list(qs)


def resolve_environments(system, env_selector=None):
    """Environments of `system`, optionally narrowed to one by name (exact)."""
    qs = system.environments.all().order_by('name')
    if env_selector:
        qs = qs.filter(name=env_selector)
    return list(qs)


def scan(system, environments):
    """
    Run a live AWS scan for each environment and record a drift snapshot,
    mirroring the web app's manual-scan flow (ScanJob → run_scan → snapshot).

    Returns a list of per-environment dicts:
        {'environment': Environment, 'result': {...}, 'failed': bool}
    """
    from asset_manager.scanner import run_scan
    from asset_manager.models import ScanJob, DriftSnapshot
    from asset_manager.views import _record_drift_snapshot

    out = []
    for env in environments:
        job = ScanJob.objects.create(
            system=system,
            status=ScanJob.Status.RUNNING,
            regions=system.aws_scan_regions or [],
            started_at=timezone.now(),
        )
        try:
            result = run_scan(system, env)
            job.status        = ScanJob.Status.DONE
            job.created_count = result['created']
            job.updated_count = result['updated']
            job.finished_at   = timezone.now()
            if result['errors']:
                job.error_message = '\n'.join(result['errors'])
            job.save()
            _record_drift_snapshot(env, DriftSnapshot.Source.SCAN)
            failed = False
        except Exception as exc:  # noqa: BLE001 - report, don't crash the CLI run
            job.status        = ScanJob.Status.FAILED
            job.error_message = str(exc)
            job.finished_at   = timezone.now()
            job.save()
            result = {'scanned': 0, 'created': 0, 'updated': 0, 'errors': [str(exc)]}
            failed = True
        out.append({'environment': env, 'result': result, 'failed': failed})
    return out


def drift_for(environment):
    """
    Current drift for one environment, computed the same way the dashboard and
    the recorded snapshots are:

        {'changed': [{type, name, cloud_id, provider, changes:[...]}, ...],
         'added':   [{type, name, cloud_id, provider}, ...],
         'unchanged': int}

    `added` = assets that have never had a previous snapshot (first sighting),
    matching the core's ADDED semantics.
    """
    from asset_manager.views import _compute_raw_diff

    changed, added, unchanged = [], [], 0
    assets = environment.assets.only(
        'asset_type', 'name', 'cloud_id', 'provider', 'raw_data', 'raw_data_prev',
    ).order_by('asset_type', 'name')

    for asset in assets:
        meta = {
            'type':     asset.asset_type,
            'name':     asset.name,
            'cloud_id': asset.cloud_id,
            'provider': asset.provider,
        }
        if not asset.raw_data_prev:
            added.append(meta)
        else:
            diff = _compute_raw_diff(asset.raw_data_prev, asset.raw_data)
            if diff:
                changed.append({**meta, 'changes': diff})
            else:
                unchanged += 1

    return {'changed': changed, 'added': added, 'unchanged': unchanged}


def status_rows():
    """
    One row per environment for the `status` subcommand:
        {'system', 'code', 'environment', 'assets', 'last_scan', 'scan_enabled'}
    `last_scan` is a tz-aware datetime or None.
    """
    from asset_manager.models import System, ScanJob

    rows = []
    systems = (
        System.objects.all()
        .order_by('name')
        .prefetch_related('environments')
    )
    for system in systems:
        last_job = (
            ScanJob.objects
            .filter(system=system, finished_at__isnull=False)
            .order_by('-finished_at')
            .first()
        )
        last_scan = last_job.finished_at if last_job else None
        env_qs = (
            system.environments.all()
            .annotate(asset_count=Count('assets'))
            .order_by('name')
        )
        for env in env_qs:
            rows.append({
                'system':       system.name,
                'code':         system.code,
                'environment':  env.name,
                'assets':       env.asset_count,
                'last_scan':    last_scan,
                'scan_enabled': system.scan_enabled,
            })
    return rows
