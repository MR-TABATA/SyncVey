from django.contrib import admin
from django.urls import path, include
from django.conf import settings

admin.autodiscover()

urlpatterns = [
    path('admin/', admin.site.urls),
    # HTMXアプリケーションのURLをインクルード
    path('', include('asset_manager.urls')),
]

if settings.DEBUG:
    urlpatterns = [
        path('__debug__/', include('debug_toolbar.urls')),
    ] + urlpatterns
