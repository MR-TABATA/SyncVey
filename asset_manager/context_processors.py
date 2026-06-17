"""
context_processors.py
---------------------
Expose the feature-flag / plugin seams to every template, so the core layout
can show or hide UI based on which features/plugins are present without any
hard dependency on optional apps.

    {% if features.drift_history %} ... {% endif %}
    {% for item in plugin_nav_items %} ... {% endfor %}
"""

from . import plugins


def features(request):
    return {
        'features': plugins.available_features(),
        'plugin_nav_items': plugins.plugin_nav_items(request),
    }
