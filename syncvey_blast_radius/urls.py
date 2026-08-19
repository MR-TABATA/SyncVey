from django.urls import path

from . import views

app_name = 'blast_radius'

urlpatterns = [
    path('blast-radius/', views.blast_radius_view, name='home'),
]
