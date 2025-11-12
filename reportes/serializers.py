from rest_framework import serializers
from venta.models import CarritoModel, PlanPagoModel, PagoModel, PedidoModel, DetallePedidoModel
from producto.models import ProductoModel, CategoriaModel , MarcaModel
from usuario.models import Usuario, Grupo

class CarritoReporteSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    
    class Meta:
        model = CarritoModel
        fields = ['id', 'usuario_username', 'fecha', 'total', 'is_active']

class PedidoReporteSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    forma_pago_nombre = serializers.CharField(source='forma_pago.nombre', read_only=True)
    
    class Meta:
        model = PedidoModel
        fields = [
            'id', 'usuario_username', 'forma_pago_nombre', 'fecha', 
            'total', 'estado', 'is_active'
        ]

class DetallePedidoReporteSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(source='producto.nombre', read_only=True)
    pedido_id = serializers.IntegerField(source='pedido.id', read_only=True)
    
    class Meta:
        model = DetallePedidoModel
        fields = [
            'id', 'pedido_id', 'producto_nombre', 'cantidad', 
            'precio_unitario', 'subtotal', 'is_active'
        ]

class PagoReporteSerializer(serializers.ModelSerializer):
    metodo_pago_nombre = serializers.CharField(source='metodo_pago.nombre', read_only=True)
    plan_pago_numero_cuota = serializers.IntegerField(source='plan_pago.numero_cuota', read_only=True)
    
    class Meta:
        model = PagoModel
        fields = [
            'id', 'fecha_pago', 'monto', 'comprobante', 
            'metodo_pago_nombre', 'plan_pago_numero_cuota', 'is_active'
        ]

class PlanPagoReporteSerializer(serializers.ModelSerializer):
    pedido_id = serializers.IntegerField(source='pedido.id', read_only=True)
    usuario_username = serializers.CharField(source='pedido.usuario.username', read_only=True)
    
    class Meta:
        model = PlanPagoModel
        fields = [
            'id', 'pedido_id', 'usuario_username', 'numero_cuota', 
            'monto', 'fecha_vencimiento', 'estado', 'is_active'
        ]
class ProductoReporteSerializer(serializers.ModelSerializer):
    marca_nombre = serializers.CharField(source='marca.nombre', read_only=True)
    subcategoria_nombre = serializers.CharField(source='subcategoria.nombre', read_only=True)
    categoria_nombre = serializers.CharField(source='subcategoria.categoria.nombre', read_only=True)
    
    class Meta:
        model = ProductoModel
        fields = [
            'id', 'nombre', 'descripcion', 'modelo', 'precio_contado', 
            'precio_cuota', 'stock', 'garantia_meses', 'fecha_registro',
            'is_active', 'marca_nombre', 'subcategoria_nombre', 'categoria_nombre'
        ]

class CategoriaReporteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaModel
        fields = ['id', 'nombre', 'descripcion', 'is_active']

class MarcaReporteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarcaModel
        fields = ['id', 'nombre', 'is_active']
class UsuarioReporteSerializer(serializers.ModelSerializer):
    grupo_nombre = serializers.CharField(source='grupo.nombre', read_only=True)
    
    class Meta:
        model = Usuario
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 
            'ci', 'telefono', 'date_joined', 'is_active', 'grupo_nombre'
        ]