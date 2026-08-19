"""
views.py
--------
One htmx partial view, gated by the ``blast_radius`` feature flag: the blast-
radius list — starting from the drift the org already has, everything that drift
can reach across the resource reference graph, ranked by impact.

Like ``syncvey_drift_risk``, the plugin reaches into the core (asset_manager)
for models and the org helper; the core never reaches back. Removing this app
from INSTALLED_APPS makes ``feature_enabled('blast_radius')`` false and this
route 404s.
"""

from django.http import Http404
from django.shortcuts import render

from asset_manager.plugins import feature_enabled
from asset_manager.views import htmx_login_required, _get_user_org

from .service import org_blast_radius

# How many impacted assets to show. A single hub drift can splash across a large
# environment; the tail past this is rarely actionable, and the score ranking
# already floats what matters to the top.
_DISPLAY_LIMIT = 50


@htmx_login_required
def blast_radius_view(request):
    if not feature_enabled('blast_radius'):
        raise Http404

    org = _get_user_org(request)
    result = (
        org_blast_radius(org)
        if org
        else {'report': [], 'source_count': 0, 'weighted': False}
    )

    report = result['report']
    # Collateral = impacted but not itself a drift source: the reason this view
    # exists beyond the plain drift list.
    collateral = sum(1 for r in report if not r.is_source)

    return render(request, 'syncvey_blast_radius/_blast_list.html', {
        'rows':         report[:_DISPLAY_LIMIT],
        'total':        len(report),
        'shown':        min(len(report), _DISPLAY_LIMIT),
        'source_count': result['source_count'],
        'collateral':   collateral,
        'weighted':     result['weighted'],
    })
