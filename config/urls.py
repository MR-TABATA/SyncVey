from django.apps import apps
from django.contrib import admin
from django.urls import path, include
from django.conf import settings

admin.autodiscover()

urlpatterns = [
    path('admin/', admin.site.urls),
    # HTMXアプリケーションのURLをインクルード
    path('', include('asset_manager.urls')),
]

# Optional plugin apps register their routes only when installed, so removing a
# plugin from INSTALLED_APPS can't leave a dangling include — the core never
# hard-depends on it.
if apps.is_installed('syncvey_drift_risk'):
    urlpatterns += [path('', include('syncvey_drift_risk.urls'))]

if apps.is_installed('syncvey_blast_radius'):
    urlpatterns += [path('', include('syncvey_blast_radius.urls'))]

# Extra plugin apps (see SYNCVEY_EXTRA_APPS) contribute their own routes by
# naming a `urls_module` on their AppConfig; the core never imports them by name.
from asset_manager.plugins import plugin_url_modules  # noqa: E402
for _urls in plugin_url_modules():
    urlpatterns += [path('', include(_urls))]

if settings.DEBUG:
    urlpatterns = [
        path('__debug__/', include('debug_toolbar.urls')),
    ] + urlpatterns
