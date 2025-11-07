from django.shortcuts import render
# from utils.encrypted_logger import registrar_accion
from comercio.permissions import requiere_permiso 
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .serializers import CategoriaSerializer, SubcategoriaSerializer, MarcaSerializer, ProductoSerializer
from .models import CategoriaModel, SubcategoriaModel, MarcaModel, ProductoModel
# Create your views here.

# CRUD CATEGORIAS
# --------------------- Crear Categoria ---------------------
@api_view(['POST'])
@requiere_permiso("Categoria", "crear")
def crear_categoria(request):
    serializer = CategoriaSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({
            "status": 1,
            "error": 0,
            "message": "Categoria creada correctamente",
            "values": {"categoria": serializer.data}
        })
    return Response({
        "status": 0,
        "error": 1,
        "message": "Error al crear Categoría",
        "values": serializer.errors
    })

# --------------------- Editar Categorias ---------------------
@api_view(['PATCH'])
@requiere_permiso("Categoria", "actualizar")
def editar_categoria(request, categoria_id):
    try:
        categoria = CategoriaModel.objects.get(id=categoria_id)
    except CategoriaModel.DoesNotExist:
        return Response({
            "status": 0,
            "error": 1,
            "message": "Categoria no encontrada",
            "values": {}
        })

    serializer = CategoriaSerializer(categoria, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response({
            "status": 1,
            "error": 0,
            "message": "Categoria editada correctamente",
            "values": {"categoria": serializer.data}
        })
    return Response({
        "status": 0,
        "error": 1,
        "message": "Error al editar Categoria",
        "values": serializer.errors
    })

# --------------------- Eliminar ( desactivar ) Categorias ---------------------
@api_view(['DELETE'])
@requiere_permiso("Categoria", "eliminar")
def eliminar_categoria(request, categoria_id):
    try:
        categoria = CategoriaModel.objects.get(id=categoria_id)
        categoria.is_active = False
        categoria.save()
    except CategoriaModel.DoesNotExist:
        return Response({
            "status": 0,
            "error": 1,
            "message": "Categoria no encontrada",
            "values": {}
        })

    return Response({
        "status": 1,
        "error": 0,
        "message": "Categoria eliminada correctamente",
        "values": {}
    })

# --------------------- Activar Categorias ---------------------
@api_view(['PATCH'])
@requiere_permiso("Categoria", "activar")
def activar_categoria(request, categoria_id):
    try:
        categoria = CategoriaModel.objects.get(id=categoria_id)
        categoria.is_active = True
        categoria.save()
    except CategoriaModel.DoesNotExist:
        return Response({
            "status": 0,
            "error": 1,
            "message": "Categoria no encontrada",
            "values": {}
        })

    return Response({
        "status": 1,
        "error": 0,
        "message": "Categoria activada correctamente",
        "values": {}
    })

# --------------------- Listar Categorias ( ACTIVAS )---------------------
@api_view(['GET'])
@requiere_permiso("Categoria", "leer")
def listar_categorias_activas(request):
    categorias = CategoriaModel.objects.filter(is_active=True)
    serializer = CategoriaSerializer(categorias, many=True)
    return Response({
        "status": 1,
        "error": 0,
        "message": "Categorias obtenidas correctamente",
        "values": {"categorias": serializer.data}
    })

# --------------------- Listar Todas las Categorias ---------------------
@api_view(['GET'])
@requiere_permiso("Categoria", "leer")
def listar_categorias(request):
    categorias = CategoriaModel.objects.all()
    serializer = CategoriaSerializer(categorias, many=True)
    return Response({
        "status": 1,
        "error": 0,
        "message": "Categorias obtenidas correctamente",
        "values": {"categorias": serializer.data}
    })

# ---------------------- Listar Categoría por ID ----------------------
@api_view(['GET'])
@requiere_permiso("Categoria", "leer")
def obtener_categoria_por_id(request, categoria_id):
    try:
        categoria = CategoriaModel.objects.get(id=categoria_id)
    except CategoriaModel.DoesNotExist:
        return Response({
            "status": 0,
            "error": 1,
            "message": "Categoria no encontrada",
            "values": {}
        })

    serializer = CategoriaSerializer(categoria)
    return Response({
        "status": 1,
        "error": 0,
        "message": "Categoria obtenida correctamente",
        "values": {"categoria": serializer.data}
    })