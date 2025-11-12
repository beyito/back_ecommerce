# reportes/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('generar', views.GenerarReporteView.as_view(), name='generar_reporte'),
    path('directo', views.ReporteDirectoView.as_view(), name='reporte_directo'),
    path('exportar', views.ExportarDatosView.as_view(), name='exportar_datos'),
]