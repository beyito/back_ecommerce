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
# En serializers.py - Agrega este serializer
class VentasAgrupadasSerializer(serializers.Serializer):
    """Serializer para datos de ventas agrupadas"""
    producto_id = serializers.IntegerField(required=False)
    producto_nombre = serializers.CharField(required=False)
    categoria_nombre = serializers.CharField(required=False, allow_null=True)
    marca_nombre = serializers.CharField(required=False, allow_null=True)
    cliente = serializers.CharField(required=False, allow_null=True)
    total_vendido = serializers.IntegerField(required=False)
    unidades_vendidas = serializers.IntegerField(required=False)
    ingresos_totales = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    promedio_venta = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)

class PedidoClienteSerializer(serializers.ModelSerializer):
    detalles = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    
    class Meta:
        model = PedidoModel
        fields = [
            'id', 'fecha', 'total', 'estado', 'estado_display', 
            'direccion_entrega', 'detalles'
        ]
    
    def get_detalles(self, obj):
        detalles = DetallePedidoModel.objects.filter(pedido=obj)
        return DetallePedidoClienteSerializer(detalles, many=True).data

class DetallePedidoClienteSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(source='producto.nombre', read_only=True)
    producto_imagen = serializers.CharField(source='producto.imagen_principal', read_only=True)
    producto_marca = serializers.CharField(source='producto.marca.nombre', read_only=True)
    
    class Meta:
        model = DetallePedidoModel
        fields = [
            'id', 'producto_nombre', 'producto_imagen', 'producto_marca',
            'cantidad', 'precio_unitario', 'subtotal'
        ]