"""
plugins.py
----------
Feature-flag + plugin discovery for SyncVey.

Goal: keep optional / advanced features **detachable**. Such a feature can
live in its own Django app and plug into the core through this module only —
the core never hard-imports an optional app. Removing the app (or flipping a
flag) degrades gracefully and never breaks the core.

Two seams:

1. Feature flags — `feature_enabled('name')`.
   Resolution order:
     a. explicit `settings.SYNCVEY_FEATURES['name']`  (env: FEATURE_NAME=...)
     b. a plugin app advertises the feature  → enabled
     c. `CORE_FEATURE_DEFAULTS['name']`               (features still in core)
     d. otherwise False
   Gating an in-core feature behind a flag now makes later extraction into a
   plugin app a no-op for callers.

2. Plugin apps — any installed Django app whose AppConfig opts in:

       class MyFeatureConfig(AppConfig):
           name = 'syncvey_myfeature'
           syncvey_plugin = True
           feature_name = 'myfeature'          # optional, defaults to app label
           def nav_items(self, request):        # optional sidebar entries
               return [{'key': 'myfeature', 'label': _('My Feature'),
                        'url': reverse('myfeature:home'), 'icon': 'sparkles'}]

   The core discovers these via Django's app registry (no import of the app
   from core code).
"""

from django.apps import apps
from django.conf import settings


# Features that live inside the core app today but are designed to be movable
# into a standalone plugin app later. Listed here so they can be toggled now;
# the value is the default-enabled state.
CORE_FEATURE_DEFAULTS = {
    'drift_history': True,
}


def _plugin_app_configs():
    """Installed apps that opted in as SyncVey plugins."""
    return [cfg for cfg in apps.get_app_configs() if getattr(cfg, 'syncvey_plugin', False)]


def _plugin_feature_names():
    """Feature names advertised by installed plugin apps."""
    return {getattr(cfg, 'feature_name', cfg.label) for cfg in _plugin_app_configs()}


def feature_enabled(name: str) -> bool:
    """Whether feature `name` is available (see resolution order in module doc)."""
    overrides = getattr(settings, 'SYNCVEY_FEATURES', {}) or {}
    if name in overrides:
        return bool(overrides[name])
    if name in _plugin_feature_names():
        return True
    return CORE_FEATURE_DEFAULTS.get(name, False)


def available_features() -> dict:
    """{feature_name: bool} for every known feature — handy for templates."""
    names = (
        set(CORE_FEATURE_DEFAULTS)
        | set(getattr(settings, 'SYNCVEY_FEATURES', {}) or {})
        | _plugin_feature_names()
    )
    return {name: feature_enabled(name) for name in names}


def plugin_nav_items(request=None) -> list:
    """
    Collect sidebar nav entries contributed by installed plugin apps.
    Each entry is a dict: {key, label, url, icon}. A failing plugin is skipped
    rather than allowed to break the core layout.
    """
    items = []
    for cfg in _plugin_app_configs():
        getter = getattr(cfg, 'nav_items', None)
        if not callable(getter):
            continue
        try:
            items.extend(getter(request) or [])
        except Exception:  # noqa: BLE001 - a broken plugin must not break the shell
            continue
    return items
