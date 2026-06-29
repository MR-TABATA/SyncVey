from django.urls import path

from . import views

app_name = 'drift_risk'

urlpatterns = [
    path('drift-risk/', views.drift_risk_view, name='home'),
    path('drift-risk/<int:asset_id>/actor/', views.drift_risk_actor_view, name='actor'),
    path('drift-digest/', views.drift_digest_preview_view, name='digest'),
]
