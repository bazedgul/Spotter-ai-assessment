"""URL configuration for fuel app."""

from django.urls import path
from fuel import views

app_name = 'fuel'

urlpatterns = [
    path('routes/', views.RouteCalculateView.as_view(), name='calculate_route'),
    path('routes/map/<str:route_id>/', views.RouteMapView.as_view(), name='route_map'),
]
