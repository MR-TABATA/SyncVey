from django.apps import AppConfig
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


class DriftRiskConfig(AppConfig):
    """
    Optional, detachable plugin: classifies drift by security risk and (lazily)
    attributes each change to the actor that made it via CloudTrail.

    It plugs into the core through asset_manager.plugins only — the core never
    imports this app. `syncvey_plugin = True` makes the core discover it; the
    `drift_risk` feature flips on simply because this app is installed (see
    plugins.feature_enabled resolution order). Remove the app from
    INSTALLED_APPS and the core hides the nav entry and 404s the routes.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'syncvey_drift_risk'
    syncvey_plugin = True
    feature_name = 'drift_risk'

    def nav_items(self, request):
        return [
            {
                'key': 'drift-risk',
                'label': _('Drift Risk'),
                'url': reverse('drift_risk:home'),
                'icon': 'shield-alert',
            },
            {
                'key': 'drift-digest',
                'label': _('Drift Briefing'),
                'url': reverse('drift_risk:digest'),
                'icon': 'mail',
            },
        ]

    def scheduled_jobs(self):
        """
        Register the weekly drift briefing — opt-in via DRIFT_DIGEST_ENABLED
        (like the EOL refresh job), so a fresh install stays quiet until the
        operator turns it on and configures a Slack webhook.
        """
        from django.conf import settings
        if not getattr(settings, 'DRIFT_DIGEST_ENABLED', False):
            return []
        from apscheduler.triggers.cron import CronTrigger
        from .digest import run_digest_job
        return [{
            'id': 'drift_digest_weekly',
            'name': 'Weekly drift briefing',
            'func': run_digest_job,
            'trigger': CronTrigger(day_of_week='mon', hour=9, minute=0),
        }]
