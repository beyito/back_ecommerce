# reportes/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('generar', views.GenerarReporteView.as_view(), name='generar_reporte'),
    path('directo', views.ReporteDirectoView.as_view(), name='reporte_directo'),
    path('exportar', views.ExportarDatosView.as_view(), name='exportar_datos'),
    path('consulta-ia/', views.consulta_ia_cliente, name='consulta-ia-cliente'),
    path('estadisticas/', views.estadisticas_cliente, name='estadisticas-cliente'),
    path('procesar-voz/', views.procesar_voz_cliente, name='procesar-voz-cliente'),
    path('opciones-filtros/', views.opciones_filtros_cliente, name='opciones-filtros-cliente'),
    path('generar-reporte/', views.generar_reporte_cliente, name='generar-reporte-cliente'),
    path('generar-pdf-reporte/', views.generar_pdf_reporte, name='generar-pdf-reporte'),
    path('generar-pdf-consulta/', views.generar_pdf_consulta_ia, name='generar-pdf-consulta-ia'),
]