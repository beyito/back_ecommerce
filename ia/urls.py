# ia/urls.py
from django.urls import path
from .views import PrediccionVentasView

urlpatterns = [
    path('prediccion-ventas/', PrediccionVentasView.as_view(), name='prediccion-ventas'),
]
