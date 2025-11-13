# reportes/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('generar', views.GenerarReporteView.as_view(), name='generar_reporte'),
    path('directo', views.ReporteDirectoView.as_view(), name='reporte_directo'),
    path('exportar', views.ExportarDatosView.as_view(), name='exportar_datos'),
    path('reportes-cliente/consulta-ia/', views.consulta_ia_cliente, name='consulta-ia-cliente'),
    path('reportes-cliente/estadisticas/', views.estadisticas_cliente, name='estadisticas-cliente'),
    path('reportes-cliente/procesar-voz/', views.procesar_voz_cliente, name='procesar-voz-cliente'),
    path('reportes-cliente/opciones-filtros/', views.opciones_filtros_cliente, name='opciones-filtros-cliente'),
    path('reportes-cliente/generar-reporte/', views.generar_reporte_cliente, name='generar-reporte-cliente'),

]