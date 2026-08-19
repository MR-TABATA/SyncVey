from django.apps import AppConfig


class SyncveyCliConfig(AppConfig):
    """
    Optional, detachable plugin: a terminal / CI front door to the same scan
    and drift engine the web UI drives — so an operator (or a pipeline) can ask
    "scan now" and "what drifted?" without opening a tab.

    Unlike the web plugins, this one's seam is Django's own management-command
    discovery: every app in INSTALLED_APPS contributes its
    `management/commands/*.py`, so installing this app grows `manage.py` a
    `syncvey` command and removing it takes the command away — the core never
    imports this app. It advertises `feature_name = 'cli'` for the feature
    registry but contributes no nav entry or route (it isn't a web surface).

    The command reuses the core scan/drift functions verbatim (plugins may
    depend on the core; the core never depends on a plugin), so the CLI can
    never disagree with the dashboard about what counts as drift.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'syncvey_cli'
    syncvey_plugin = True
    feature_name = 'cli'
