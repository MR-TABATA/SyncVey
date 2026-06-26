"""
cloudtrail.py
-------------
Attribution: who last changed a resource, and when. A read-only
``cloudtrail:LookupEvents`` against the account SyncVey already assumes a role
into. It degrades to ``None`` on any failure (missing permission, throttling, no
trail, resource not found) — attribution is a bonus, never load-bearing.

Caveats worth knowing (and surfaced honestly in the UI):
- CloudTrail Lookup covers ~90 days of management events only.
- It's regional; we query the system's first scan region.
- Console/API writes are captured; some service-internal changes are not.
"""

import json
import logging

logger = logging.getLogger(__name__)

# events that merely *read* state — never the culprit for a change
_READ_PREFIXES = ('Describe', 'List', 'Get', 'Lookup', 'BatchGet')


def _parse_event(ev):
    """Pull actor / ip out of a CloudTrail event record."""
    detail = {}
    raw = ev.get('CloudTrailEvent')
    if raw:
        try:
            detail = json.loads(raw)
        except (ValueError, TypeError):
            detail = {}

    identity = detail.get('userIdentity', {}) or {}
    user = (
        ev.get('Username')
        or identity.get('userName')
        or identity.get('arn')
        or identity.get('type')
        or 'unknown'
    )
    return {
        'user':      user,
        'event':     ev.get('EventName', ''),
        'time':      ev.get('EventTime'),
        'source_ip': detail.get('sourceIPAddress'),
    }


def lookup_actor(session, resource_id, max_results=10):
    """
    Return ``{'user','event','time','source_ip'}`` for the most recent *mutating*
    management event on ``resource_id``, or ``None`` if nothing usable is found.
    """
    if not session or not resource_id:
        return None
    try:
        client = session.client('cloudtrail')
        resp = client.lookup_events(
            LookupAttributes=[{
                'AttributeKey': 'ResourceName',
                'AttributeValue': resource_id,
            }],
            MaxResults=max_results,
        )
    except Exception as exc:  # noqa: BLE001 - attribution must never break the page
        logger.warning('CloudTrail lookup failed for %s: %s', resource_id, exc)
        return None

    for ev in resp.get('Events', []):
        name = ev.get('EventName', '')
        if name.startswith(_READ_PREFIXES):
            continue  # skip read noise; we want who *changed* it
        return _parse_event(ev)
    return None
