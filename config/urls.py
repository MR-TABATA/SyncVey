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

if settings.DEBUG:
    urlpatterns = [
        path('__debug__/', include('debug_toolbar.urls')),
    ] + urlpatterns
