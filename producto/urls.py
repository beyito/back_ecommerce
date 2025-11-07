# urls.py
from django.urls import path
# from rest_framework_simplejwt.views import TokenRefreshView
from . import views
urlpatterns = [
    path('crear_categoria', views.crear_categoria, name='crear_categoria'),
    path('editar_categoria/<int:categoria_id>', views.editar_categoria, name='editar_categoria'),
    path('eliminar_categoria/<int:categoria_id>', views.eliminar_categoria, name='eliminar_categoria'),
    path('activar_categoria/<int:categoria_id>', views.activar_categoria, name='activar_categoria'),
    path('listar_activas', views.listar_categorias_activas, name='listar_categorias_activas'),
    path('listar_categorias', views.listar_categorias, name='listar_categorias'),
    path('obtener_categoria/<int:categoria_id>/', views.obtener_categoria_por_id, name='obtener_categoria_por_id'),
]