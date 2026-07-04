"""
rotation.py
-----------
Secrets Manager rotation risk — a *different shape* of drift from rules.py.

rules.py grades a field-level diff: something changed, how bad is the change.
This module grades a **standing condition**: a rotation that should have
happened but didn't. There is no old→new diff for "this secret hasn't rotated
in 200 days" — it's true even when nothing changed between two scans. So we
read the secret's current scanned metadata (never the value) and grade it.

Pure functions over a dict — no AWS calls, no models — so it stays fast and
exhaustively testable, same discipline as rules.py.
"""

from datetime import datetime

from django.utils.translation import gettext_lazy as _

from .rules import CRITICAL, HIGH, LOW, MEDIUM, SEVERITY_ORDER

_TRUTHY = {'true', '1', 'yes', 'enabled', 'on'}


def _truthy(v):
    return str(v if v is not None else '').strip().lower() in _TRUTHY


def _parse(value):
    """ISO string (from scan_secrets) → aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def assess_rotation(raw_data, now, max_age_days):
    """
    Grade one secret's rotation posture.

    Returns ``{'severity', 'findings': [{field, severity, reason}, ...]}`` in
    the same shape as rules.assess(), so rows drop straight into the risk list.
    A healthy secret returns an empty findings list (LOW).
    """
    raw = raw_data or {}
    findings = []

    if not _truthy(raw.get('rotation_enabled')):
        findings.append({
            'field':    'rotation_enabled',
            'severity': HIGH,
            'reason':   _('Automatic rotation is disabled'),
        })
        return _wrap(findings)

    last = _parse(raw.get('last_rotated_date'))
    if last is None:
        findings.append({
            'field':    'last_rotated_date',
            'severity': MEDIUM,
            'reason':   _('Rotation is enabled but the secret has never rotated'),
        })
        return _wrap(findings)

    age_days = (now - last).days
    if age_days >= max_age_days * 2:
        findings.append({
            'field':    'last_rotated_date',
            'severity': CRITICAL,
            'reason':   _('Not rotated in %(age)d days — over twice the %(limit)d-day limit')
                        % {'age': age_days, 'limit': max_age_days},
        })
    elif age_days >= max_age_days:
        findings.append({
            'field':    'last_rotated_date',
            'severity': HIGH,
            'reason':   _('Not rotated in %(age)d days (limit %(limit)d)')
                        % {'age': age_days, 'limit': max_age_days},
        })

    return _wrap(findings)


def _wrap(findings):
    worst = LOW
    for f in findings:
        if SEVERITY_ORDER[f['severity']] > SEVERITY_ORDER[worst]:
            worst = f['severity']
    return {'severity': worst, 'findings': findings}
