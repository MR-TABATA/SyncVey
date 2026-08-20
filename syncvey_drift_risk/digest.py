"""
digest.py
---------
The "Monday-morning briefing": a periodic rollup of a system's drift that
synthesizes everything the tool already records — the drift *history* (trend),
the severity *rules*, and CloudTrail *attribution* — into one push.

A stateless `terraform plan` can't produce this: it has no history to trend
against and no idea who touched the console. We do.

build_digest()  : compute the briefing for one system (no side effects)
send_digest()   : build it and post to the system's Slack webhook
run_digest_job(): the scheduled entry point — every system with a webhook
"""

import logging
from datetime import timedelta

from django.utils import timezone

from asset_manager.models import DriftSnapshot, Environment, System

from .rules import assess, SEVERITY_ORDER, CRITICAL, HIGH, MEDIUM, LOW

logger = logging.getLogger(__name__)


def _attach_actors(system, items):
    """Best-effort CloudTrail attribution for the named items (one session)."""
    if not system.aws_role_arn:
        return
    try:
        from asset_manager.scanner import get_session
        from .cloudtrail import lookup_actor
        region = (system.aws_scan_regions or ['ap-northeast-1'])[0]
        session = get_session(system.aws_role_arn, region=region)
    except Exception:  # noqa: BLE001
        return
    for it in items:
        if it.get('cloud_id'):
            actor = lookup_actor(session, it['cloud_id'])
            if actor:
                it['actor'] = actor


def build_digest(system, days=7, attribute=True, top_n=5):
    """
    Return the briefing for ``system`` over the trailing ``days``:

        {system, window_days, total_now, delta (vs window start | None),
         severity_counts, top: [{env,name,type,cloud_id,severity,reason,actor}],
         has_data}
    """
    cutoff = timezone.now() - timedelta(days=days)
    severity_counts = {CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0}
    top, total_now, total_prev, has_prev = [], 0, 0, False

    for env in Environment.objects.filter(system=system):
        latest = (
            DriftSnapshot.objects.filter(environment=env)
            .order_by('-detected_at').first()
        )
        if latest:
            # total_count は removed も含む。ここで手計算すると、区分が
            # 増えるたびに数え落とす（実際 removed_count で落とした）。
            total_now += latest.total_count
            for item in (latest.detail or {}).get('changed', []):
                result = assess(item.get('type', ''), item.get('changes', []))
                severity_counts[result['severity']] += 1
                if SEVERITY_ORDER[result['severity']] >= SEVERITY_ORDER[HIGH]:
                    top.append({
                        'env':      env.name,
                        'name':     item.get('name'),
                        'type':     item.get('type'),
                        'cloud_id': item.get('cloud_id'),
                        'severity': result['severity'],
                        'reason':   result['findings'][0]['reason'] if result['findings'] else '',
                        'actor':    None,
                    })
        # window-start snapshot for the trend arrow
        prev = (
            DriftSnapshot.objects.filter(environment=env, detected_at__lte=cutoff)
            .order_by('-detected_at').first()
        )
        if prev:
            has_prev = True
            total_prev += prev.total_count

    top.sort(key=lambda t: SEVERITY_ORDER[t['severity']], reverse=True)
    top = top[:top_n]
    if attribute:
        _attach_actors(system, top)

    return {
        'system':          system,
        'window_days':     days,
        'total_now':       total_now,
        'delta':           (total_now - total_prev) if has_prev else None,
        'severity_counts': severity_counts,
        'top':             top,
        'has_data':        total_now > 0 or any(severity_counts.values()),
    }


def _format_slack(digest):
    """Render a digest into a Slack Block Kit payload."""
    system = digest['system']
    sc = digest['severity_counts']
    delta = digest['delta']
    if delta is None:
        trend = "no prior baseline"
    elif delta > 0:
        trend = f"▲ {delta} since last week"
    elif delta < 0:
        trend = f"▼ {abs(delta)} since last week"
    else:
        trend = "no change since last week"

    header = f"Drift briefing — {system.name}"
    summary = (f"*{digest['total_now']}* drifted  ·  {trend}\n"
               f":red_circle: {sc[CRITICAL]} critical  "
               f":large_orange_circle: {sc[HIGH]} high  "
               f":large_yellow_circle: {sc[MEDIUM]} medium  "
               f":white_circle: {sc[LOW]} low")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
    ]
    if digest['top']:
        blocks.append({"type": "divider"})
        for it in digest['top']:
            who = ''
            if it.get('actor'):
                who = f"\n_changed by *{it['actor']['user']}*_"
                if it['actor'].get('event'):
                    who += f" via `{it['actor']['event']}`"
            line = (f"*{it['severity'].upper()}*  {it['name']} "
                    f"(`{it['type']}` / {it['env']})\n{it['reason']}{who}")
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line}})

    return {"blocks": blocks}


def send_digest(system, days=7):
    """Build the briefing and post it to the system's Slack webhook.

    Returns True only if a message was actually sent. Stays quiet when there's
    no webhook or no drift worth reporting (a clean week shouldn't ping anyone).
    """
    from asset_manager.notifications import _post_to_slack, _is_allowed_webhook
    url = system.slack_webhook_url
    if not url or not _is_allowed_webhook(url):
        return False
    digest = build_digest(system, days=days, attribute=True)
    if not digest['has_data']:
        return False
    return _post_to_slack(url, _format_slack(digest))


def run_digest_job():
    """Scheduled entry point: send a briefing for every system with a webhook."""
    systems = (
        System.objects
        .exclude(slack_webhook_url__isnull=True)
        .exclude(slack_webhook_url='')
    )
    sent = 0
    for system in systems:
        try:
            if send_digest(system):
                sent += 1
        except Exception:  # noqa: BLE001 - one bad system must not abort the run
            logger.exception("Drift digest failed for system %s", system.pk)
    logger.info("Drift digest job: sent %s briefing(s).", sent)
    return sent
