"""
notifications.py
----------------
Drift 検知時の通知送信。
現在対応: Slack Incoming Webhook
"""

import json
import logging
import urllib.request
import urllib.error
from urllib.parse import urlparse

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

# Slack Incoming Webhook は常に https://hooks.slack.com/... 。
# ユーザーが system 毎に設定できるため、SSRF / file:// 等を防ぐよう厳格に制限する。
ALLOWED_WEBHOOK_HOST = "hooks.slack.com"


def _is_allowed_webhook(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname == ALLOWED_WEBHOOK_HOST


def send_drift_notification(system, environment, result: dict) -> bool:
    """
    Drift があれば Slack に通知する。

    優先順位:
        1. system.slack_webhook_url（システム個別）
        2. settings.SLACK_WEBHOOK_URL（グローバル共通）

    Returns:
        True: 送信成功 / False: 送信スキップまたは失敗
    """
    webhook_url = system.slack_webhook_url or getattr(settings, 'SLACK_WEBHOOK_URL', None)
    if not webhook_url:
        return False

    if not _is_allowed_webhook(webhook_url):
        # URL自体はトークンを含むためログに出さない
        logger.warning(
            "Blocked webhook with disallowed scheme/host for system %s (possible SSRF)",
            getattr(system, 'code', '?'),
        )
        return False

    created = result.get('created', 0)
    updated = result.get('updated', 0)

    if created == 0 and updated == 0:
        return False  # Drift なし → 送らない

    # 通知言語はシステム設定（DB）で固定 — Web/スケジューラのどちらから起動しても一貫させる。
    lang = getattr(system, 'slack_language', None) or 'en'
    with translation.override(lang):
        message = _build_slack_message(system, environment, created, updated)
    return _post_to_slack(webhook_url, message)


def _build_slack_message(system, environment, created: int, updated: int) -> dict:
    lines = []
    if updated:
        lines.append(_("• *%(n)s changed* — drift detected between definition and actual state") % {"n": updated})
    if created:
        lines.append(_("• *%(n)s added* — resources found outside Terraform management") % {"n": created})

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 SyncVey — %s" % _("Drift Detected"),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": "*%s*\n%s" % (_("System"), system.name)},
                    {"type": "mrkdwn", "text": "*%s*\n%s (%s)" % (_("Environment"), environment.name, environment.env_type)},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(lines),
                },
            },
        ]
    }


def _post_to_slack(webhook_url: str, payload: dict) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # nosec B310: URL は _is_allowed_webhook で https + hooks.slack.com に限定済み（SSRF/file:対策）
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            return resp.status == 200
    except urllib.error.URLError as e:
        logger.warning("Slack notification failed: %s", e)
        return False
