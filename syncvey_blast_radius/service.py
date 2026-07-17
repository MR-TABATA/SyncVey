"""
service.py
----------
Step 5 of blast-radius: the thin bridge from live SyncVey data to the pure
graph/propagate/score lib (steps 1–4).

Two layers, deliberately split so the interesting logic stays testable without a
database:

- :func:`drifted_source_weights` is *pure*. Given asset-like records plus two
  injected callables — a field-diff function and an optional severity grader —
  it returns the ``{cloud_id: weight}`` map that :func:`score.impact_report`
  wants: which resources actually drifted, and how bad each drift is.

- :func:`org_blast_radius` is the Django glue. It pulls an org's assets, borrows
  the core's ``_compute_raw_diff`` and (if the ``syncvey_drift_risk`` plugin is
  installed) its severity grader, and runs the whole step-2→4 pipeline.

The severity grader is a *soft* dependency: with ``syncvey_drift_risk`` present,
a drift's blast radius is weighted by its security severity (a 0.0.0.0/0 opening
outweighs a tag edit); without it, every drift counts the same. So this app is
useful alone and richer alongside drift-risk — neither hard-imports the other.
"""

from .score import impact_report, severity_weight


def drifted_source_weights(assets, diff_fn, grade_fn=None) -> dict:
    """
    Pure: ``{cloud_id: weight}`` for every asset whose ``raw_data_prev`` →
    ``raw_data`` diff is a real change — the drifted sources of a blast radius.

    - ``diff_fn(old, new)`` returns the list of field changes (empty = no drift).
      An asset with no prior snapshot (``raw_data_prev`` falsy) is newly seen,
      not drifted, so it is skipped.
    - ``grade_fn(asset_type, changes)`` optionally grades the drift to a severity
      string; the worst grade sets the source weight via
      :func:`score.severity_weight`. Omit it and every drift weighs the same
      (1.0), so the radius ranks purely on graph proximity.
    """
    weights = {}
    for asset in assets:
        prev = getattr(asset, 'raw_data_prev', None)
        if not prev:
            continue
        changes = diff_fn(prev, getattr(asset, 'raw_data', {}) or {})
        if not changes:
            continue
        cid = getattr(asset, 'cloud_id', None)
        if not cid:
            continue
        if grade_fn is not None:
            weights[cid] = severity_weight(grade_fn(asset.asset_type, changes))
        else:
            weights[cid] = 1.0
    return weights


def _drift_risk_grader():
    """
    The ``syncvey_drift_risk`` severity grader if that plugin is installed, else
    ``None``. Soft dependency: import failure (plugin absent) degrades to
    proximity-only weighting rather than breaking the blast-radius view.
    """
    from django.apps import apps
    if not apps.is_installed('syncvey_drift_risk'):
        return None
    try:
        from syncvey_drift_risk.rules import assess
    except Exception:  # noqa: BLE001 - a broken optional plugin must not break us
        return None
    return lambda asset_type, changes: assess(asset_type, changes)['severity']


def org_blast_radius(org, decay=0.5, max_hops=None) -> dict:
    """
    Run the full step-2→4 pipeline for one organization's live assets.

    Returns ``{'report': [ScoredAsset, ...], 'source_count': int,
    'weighted_sources': {...}, 'weighted': bool}`` — the impacted assets ranked
    hardest-hit first, how many distinct drifts seed the radius, and whether
    severity weighting (drift-risk) was in play. An org with no drift yields an
    empty report.
    """
    from asset_manager.models import Asset
    from asset_manager.views import _compute_raw_diff

    assets = list(
        Asset.objects
        .filter(environment__system__organization=org)
        .select_related('environment', 'environment__system')
        .only('name', 'asset_type', 'cloud_id', 'provider',
              'raw_data', 'raw_data_prev', 'environment_id',
              'environment__name', 'environment__system__name',
              'environment__system_id')
    )

    grade_fn = _drift_risk_grader()
    weights = drifted_source_weights(assets, _compute_raw_diff, grade_fn)
    report = impact_report(assets, weights, decay=decay, max_hops=max_hops)

    return {
        'report':           report,
        'weighted_sources': weights,
        'source_count':     len(weights),
        'weighted':         grade_fn is not None,
    }
