from django.apps import AppConfig
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


class BlastRadiusConfig(AppConfig):
    """
    Optional, detachable plugin: given the drift the org already has, walk the
    resource reference graph outward and rank everything that drift can reach —
    the *blast radius* — by severity-weighted, distance-decayed impact.

    It plugs into the core through asset_manager.plugins only — the core never
    imports this app. `syncvey_plugin = True` makes the core discover it; the
    `blast_radius` feature flips on simply because the app is installed. Remove
    it from INSTALLED_APPS and the core hides the nav entry and 404s the route.

    Severity weighting is a soft dependency on `syncvey_drift_risk` (see
    service.py): richer with it, still useful without it.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'syncvey_blast_radius'
    syncvey_plugin = True
    feature_name = 'blast_radius'

    def nav_items(self, request):
        return [
            {
                'key': 'blast-radius',
                'label': _('Blast Radius'),
                'url': reverse('blast_radius:home'),
                'icon': 'waypoints',
            },
        ]
