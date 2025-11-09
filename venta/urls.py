# urls.py
from django.urls import path
# from rest_framework_simplejwt.views import TokenRefreshView
from . import views
urlpatterns = [
# CRUD CATEGORIA
    path('agregar_producto_carrito', views.agregar_producto_carrito, name='agregar_producto_carrito'),
]