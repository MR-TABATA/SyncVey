"""
rules.py
--------
Security-risk classification for drift, expressed purely over the field-level
diff that the core already computes (``[{'field','old','new'}, ...]``). No AWS
calls, no models — just rules, so it's fast and exhaustively testable.

The point is to stop treating every drift the same: a security group opening to
0.0.0.0/0 is an incident; a tag change is noise. We grade each changed field and
take the worst as the resource's severity.
"""

from django.utils.translation import gettext_lazy as _

CRITICAL = 'critical'
HIGH     = 'high'
MEDIUM   = 'medium'
LOW      = 'low'

# higher = worse; used to pick the worst finding and to sort the list
SEVERITY_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}

_TRUTHY = {'true', '1', 'yes', 'enabled', 'on'}
_FALSY  = {'', 'false', '0', 'no', 'none', 'null', 'disabled', 'off'}


def classify_change(asset_type, field, old, new):
    """Grade a single field change → (severity, human reason)."""
    f     = (field or '').lower()
    old_s = str(old if old is not None else '').lower()
    new_s = str(new if new is not None else '').lower()

    # ── network exposure ────────────────────────────────────────────────
    if '0.0.0.0/0' in new_s and '0.0.0.0/0' not in old_s:
        return CRITICAL, _('Opened to the entire internet (0.0.0.0/0)')
    if '::/0' in new_s and '::/0' not in old_s:
        return CRITICAL, _('Opened to the entire internet (IPv6 ::/0)')
    if any(k in f for k in ('public', 'publicly_accessible')) \
            and new_s in _TRUTHY and old_s not in _TRUTHY:
        return CRITICAL, _('Resource was made publicly accessible')

    # ── encryption turned off ───────────────────────────────────────────
    if any(k in f for k in ('encrypt', 'kms', 'sse')) \
            and old_s not in _FALSY and new_s in _FALSY:
        return HIGH, _('Encryption was disabled')

    # ── access / permission surface ─────────────────────────────────────
    if any(k in f for k in ('policy', 'iam', 'role', 'principal', 'acl', 'grant')):
        return HIGH, _('Access or permission configuration changed')

    # ── safety / availability nets switched off ─────────────────────────
    if any(k in f for k in ('deletion_protection', 'termination_protection',
                            'multi_az', 'backup', 'logging', 'versioning')) \
            and old_s in _TRUTHY and new_s in _FALSY:
        return MEDIUM, _('A safety, backup, or availability setting was turned off')

    return LOW, _('Configuration value changed')


def assess(asset_type, changes):
    """
    Grade every change on a resource.

    Returns ``{'severity': worst, 'findings': [{field, severity, reason}, ...]}``.
    An empty change list grades LOW with no findings.
    """
    findings = []
    worst = LOW
    for c in changes:
        sev, reason = classify_change(asset_type, c.get('field'), c.get('old'), c.get('new'))
        findings.append({'field': c.get('field'), 'severity': sev, 'reason': reason})
        if SEVERITY_ORDER[sev] > SEVERITY_ORDER[worst]:
            worst = sev
    return {'severity': worst, 'findings': findings}
