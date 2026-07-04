"""
views.py
--------
Two htmx partial views, both gated by the ``drift_risk`` feature flag:

- ``drift_risk_view``     : the risk list — every *changed* resource across the
                            org, graded by severity (cheap, no AWS calls).
- ``drift_risk_actor_view``: lazy per-resource attribution — only here do we
                            actually call CloudTrail, and only when the user
                            clicks "Who changed this?" on a row.

The plugin reaches into the core (asset_manager) for models and a few helpers;
the core never reaches back. Removing this app from INSTALLED_APPS makes
``feature_enabled('drift_risk')`` false and these routes 404.
"""

from collections import Counter

from django.conf import settings
from django.http import Http404
from django.shortcuts import render, get_object_or_404
from django.utils import timezone

from asset_manager.models import Asset
from asset_manager.plugins import feature_enabled
from asset_manager.views import htmx_login_required, _get_user_org, _compute_raw_diff
from asset_manager.scanner import get_session

from .rules import assess, SEVERITY_ORDER
from .rotation import assess_rotation


@htmx_login_required
def drift_risk_view(request):
    if not feature_enabled('drift_risk'):
        raise Http404

    org = _get_user_org(request)
    rows = []
    if org:
        assets = (
            Asset.objects
            .filter(environment__system__organization=org)
            .select_related('environment', 'environment__system')
            .only('name', 'asset_type', 'cloud_id', 'provider',
                  'raw_data', 'raw_data_prev',
                  'environment__name', 'environment__system__name',
                  'environment__system_id')
        )
        now = timezone.now()
        max_age = settings.SECRET_ROTATION_MAX_AGE_DAYS
        for asset in assets:
            findings = []
            has_change = False

            # Change-based risk: grade the field diff (needs a prior snapshot).
            if asset.raw_data_prev:
                changes = _compute_raw_diff(asset.raw_data_prev, asset.raw_data)
                if changes:
                    findings.extend(assess(asset.asset_type, changes)['findings'])
                    has_change = True

            # Standing risk: a secret rotation that should have happened but
            # didn't — evaluated on current state, no diff required.
            if (asset.raw_data or {}).get('_resource_type') == 'aws_secretsmanager_secret':
                findings.extend(assess_rotation(asset.raw_data, now, max_age)['findings'])

            if not findings:
                continue

            severity = max((f['severity'] for f in findings),
                           key=lambda s: SEVERITY_ORDER[s])
            rows.append({
                'asset':      asset,
                'severity':   severity,
                'findings':   findings,
                'has_change': has_change,
            })
        rows.sort(key=lambda r: SEVERITY_ORDER[r['severity']], reverse=True)

    counts = Counter(r['severity'] for r in rows)
    return render(request, 'syncvey_drift_risk/_risk_list.html', {
        'rows':   rows,
        'counts': counts,
        'total':  len(rows),
    })


@htmx_login_required
def drift_risk_actor_view(request, asset_id):
    if not feature_enabled('drift_risk'):
        raise Http404

    org = _get_user_org(request)
    if org is None:
        raise Http404
    asset = get_object_or_404(
        Asset.objects.select_related('environment', 'environment__system'),
        pk=asset_id, environment__system__organization=org,
    )
    system = asset.environment.system

    actor, error = None, None
    if not system.aws_role_arn:
        error = 'no-role'
    else:
        # CloudTrail is regional; query the system's first configured scan region.
        region = (system.aws_scan_regions or ['ap-northeast-1'])[0]
        try:
            from .cloudtrail import lookup_actor
            session = get_session(system.aws_role_arn, region=region)
            actor = lookup_actor(session, asset.cloud_id)
        except Exception:  # noqa: BLE001 - never break the row over attribution
            error = 'lookup-failed'

    return render(request, 'syncvey_drift_risk/_risk_actor.html', {
        'asset': asset,
        'actor': actor,
        'error': error,
    })


@htmx_login_required
def drift_digest_preview_view(request):
    """
    Preview the weekly drift briefing per system, in-app, without waiting for
    the cron or wiring Slack. Attribution is skipped here (it's a synchronous
    page render) — the delivered Slack briefing names names; this previews the
    shape, severity, and trend.
    """
    if not feature_enabled('drift_risk'):
        raise Http404

    from asset_manager.models import System
    from .digest import build_digest

    org = _get_user_org(request)
    systems = System.objects.filter(organization=org) if org else System.objects.none()
    digests = [build_digest(system, attribute=False) for system in systems]
    return render(request, 'syncvey_drift_risk/_digest_preview.html', {
        'digests': digests,
    })
