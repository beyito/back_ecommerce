from rest_framework import serializers
from .models import ProductoModel, CategoriaModel, MarcaModel, SubcategoriaModel, CambioPrecioModel

# SERIALIZER PARA CATEGORÍA DE PRODUCTO
class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaModel
        fields = ['id', 'nombre', 'descripcion', 'is_active']
        read_only_fields = ['id']

# SERIALIZER PARA SUBCATEGORÍA DE PRODUCTO
class SubcategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubcategoriaModel
        fields = ['id', 'nombre', 'categoria', 'is_active']
        read_only_fields = ['id']
    
# SERIALIZER PARA MARCA DE PRODUCTO
class MarcaSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarcaModel
        fields = ['id', 'nombre', 'is_active']
        read_only_fields = ['id']

# SERIALIZER PARA PRODUCTO
class ProductoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductoModel
        fields = ['id', 'categoria', 'marca', 'nombre', 'descripcion', 'modelo', 'precio_contado', 'precio_cuota', 'stock', 'garantia_meses', 'fecha_registro', 'is_active']
        read_only_fields = ['id']