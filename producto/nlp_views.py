# producto/nlp_views.py - VERSIÓN COMPLETA

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpRequest, QueryDict
from django.db.models import Q
from django.contrib.auth import get_user_model
from .nlp_utils import parse_ecommerce_query
from .models import ProductoModel
from .views import buscar_productos
from venta.models import CarritoModel, DetalleCarritoModel
from .serializers import ProductoSerializer

Usuario = get_user_model()

class BusquedaNaturalView(APIView):
    permission_classes = []
    
    def post(self, request):
        """
        Procesa solicitudes naturales para buscar o agregar productos al carrito
        """
        query_text = request.data.get('q', '').strip()
        usuario_id = request.data.get('usuario_id')
        
        print(f"=== SOLICITUD NLP ===")
        print(f"Texto: {query_text}")
        print(f"Usuario ID: {usuario_id}")
        
        if not query_text:
            return Response({
                "status": 0,
                "error": 1,
                "message": "Se requiere una consulta de texto",
                "values": {}
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # 1. Analizar la consulta natural
        filters = parse_ecommerce_query(query_text)
        print(f"🔍 Filtros NLP: {filters}")
        
        # 2. Determinar la acción
        accion = filters.get('accion', 'buscar')
        print(f"🎯 Acción determinada: {accion}")
        
        # 3. VERIFICAR SI HAY USUARIO_ID PARA AGREGAR AL CARRITO
        if accion == 'agregar_carrito':
            if not usuario_id:
                print("⚠️  No hay usuario_id, forzando búsqueda")
                return self._procesar_busqueda_integrada(filters, query_text, request)
            else:
                print(f"✅ Agregando al carrito para usuario {usuario_id}")
                return self._procesar_agregar_carrito(filters, usuario_id, query_text)
        else:
            print("🔍 Realizando búsqueda integrada")
            return self._procesar_busqueda_integrada(filters, query_text, request)
    
    def _procesar_busqueda_integrada(self, filters, query_text, original_request):
        """Usa la vista de búsqueda existente con los filtros de NLP"""
        # Crear un request GET simulado para tu vista de búsqueda
        simulated_request = HttpRequest()
        simulated_request.method = 'GET'
        simulated_request.user = original_request.user
        
        # Convertir filtros NLP a parámetros GET para tu vista existente
        get_params = self._convertir_filtros_a_get_params(filters, query_text)
        simulated_request.GET = get_params
        
        print(f"🔍 Parámetros GET para búsqueda: {dict(get_params)}")
        
        # Llamar a tu vista de búsqueda existente
        return buscar_productos(simulated_request)
    
    def _convertir_filtros_a_get_params(self, filters, query_text):
        """Convierte los filtros de NLP a parámetros GET para tu vista de búsqueda"""
        from django.http import QueryDict
        
        query_dict = QueryDict(mutable=True)
        
        # Usar el texto original como término de búsqueda
        query_dict['search'] = query_text
        
        # Convertir filtros específicos
        if filters.get('producto_nombre'):
            query_dict['search'] = filters['producto_nombre']
        
        if filters.get('marca'):
            # Aquí necesitarías mapear nombres de marca a IDs
            marca_id = self._obtener_id_marca(filters['marca'])
            if marca_id:
                query_dict['marca'] = str(marca_id)
        
        if filters.get('categoria'):
            # Mapear nombres de categoría a IDs
            categoria_id = self._obtener_id_categoria(filters['categoria'])
            if categoria_id:
                query_dict['categoria'] = str(categoria_id)
        
        if filters.get('precio_maximo'):
            query_dict['max_precio'] = str(filters['precio_maximo'])
        
        # Siempre mostrar productos activos y en stock para búsquedas naturales
        query_dict['activos'] = 'true'
        query_dict['en_stock'] = 'true'
        
        return query_dict
    
    def _obtener_id_marca(self, nombre_marca):
        """Convierte nombre de marca a ID"""
        from .models import MarcaModel
        try:
            marca = MarcaModel.objects.filter(
                nombre__iexact=nombre_marca, 
                is_active=True
            ).first()
            return marca.id if marca else None
        except:
            return None
    
    def _obtener_id_categoria(self, nombre_categoria):
        """Convierte nombre de categoría a ID"""
        from .models import CategoriaModel
        try:
            categoria = CategoriaModel.objects.filter(
                nombre__iexact=nombre_categoria,
                is_active=True
            ).first()
            return categoria.id if categoria else None
        except:
            return None
    
    def _procesar_agregar_carrito(self, filters, usuario_id, query_text):
        """Procesa agregado de productos al carrito (igual que antes)"""
        try:
            usuario = Usuario.objects.get(id=usuario_id, is_active=True)
            print(f"✅ Usuario encontrado: {usuario.username}")
            
            # Buscar productos que coincidan EXACTAMENTE
            productos = self._buscar_productos_exactos(filters)
            print(f"🔍 Productos encontrados: {productos.count()}")
            
            if not productos:
                return Response({
                    "status": 0,
                    "error": 1,
                    "message": f"No se encontraron productos que coincidan con: {query_text}",
                    "values": {}
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Obtener o crear carrito activo
            carrito, created = CarritoModel.objects.get_or_create(
                usuario=usuario,
                is_active=True,
                defaults={'total': 0}
            )
            print(f"🛒 Carrito {'creado' if created else 'encontrado'}: {carrito.id}")
            
            resultados_agregados = []
            producto_agregado = None
            
            # Tomar el producto más relevante (el primero)
            producto = productos[0]
            cantidad = filters.get('cantidad', 1)
            
            print(f"📦 Intentando agregar: {producto.nombre} x {cantidad}")
            
            # Verificar stock
            if producto.stock < cantidad:
                resultados_agregados.append({
                    "producto": producto.nombre,
                    "agregado": False,
                    "mensaje": f"Stock insuficiente. Disponible: {producto.stock}"
                })
            else:
                # Agregar o actualizar detalle del carrito
                detalle, created = DetalleCarritoModel.objects.get_or_create(
                    carrito=carrito,
                    producto=producto,
                    is_active=True,
                    defaults={
                        'cantidad': cantidad,
                        'precio_unitario': producto.precio_contado,
                        'subtotal': producto.precio_contado * cantidad
                    }
                )
                
                if not created:
                    detalle.cantidad += cantidad
                    detalle.subtotal = detalle.cantidad * detalle.precio_unitario
                    detalle.save()
                
                resultados_agregados.append({
                    "producto": producto.nombre,
                    "agregado": True,
                    "cantidad": detalle.cantidad,
                    "subtotal": float(detalle.subtotal),
                    "precio_unitario": float(detalle.precio_unitario)
                })
                
                producto_agregado = producto
            
            # Recalcular total del carrito
            carrito.calcular_total()
            
            # Serializar el producto agregado para la respuesta
            producto_data = None
            if producto_agregado:
                producto_data = ProductoSerializer(producto_agregado).data
            
            return Response({
                "status": 1,
                "error": 0,
                "message": f"✅ Producto agregado al carrito: {query_text}",
                "values": {
                    "resultados": resultados_agregados,
                    "producto_agregado": producto_data,
                    "carrito_id": carrito.id,
                    "total_carrito": float(carrito.total),
                    "filtros_nlp": filters,
                    "accion": "agregar_carrito"
                }
            })
            
        except Usuario.DoesNotExist:
            print(f"❌ Usuario no encontrado: {usuario_id}")
            return Response({
                "status": 0,
                "error": 1,
                "message": "Usuario no encontrado",
                "values": {}
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"❌ Error inesperado: {e}")
            return Response({
                "status": 0,
                "error": 1,
                "message": f"Error al agregar al carrito: {str(e)}",
                "values": {}
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _buscar_productos_exactos(self, filters):
        """Busca productos con criterios específicos para agregar al carrito"""
        q_objects = Q(is_active=True, stock__gt=0)
        
        producto_nombre = filters.get('producto_nombre', '').lower()
        marca = filters.get('marca', '').lower()
        caracteristicas = filters.get('caracteristicas', [])
        
        print(f"🔍 Buscando: producto='{producto_nombre}', marca='{marca}', caracteristicas={caracteristicas}")
        
        # Búsqueda por nombre de producto
        if producto_nombre:
            q_objects &= (
                Q(nombre__icontains=producto_nombre) |
                Q(descripcion__icontains=producto_nombre) |
                Q(subcategoria__nombre__icontains=producto_nombre)
            )
        
        # Búsqueda por marca (EXACTA)
        if marca:
            q_objects &= Q(marca__nombre__iexact=marca)
        
        # Búsqueda por características
        if caracteristicas:
            for carac in caracteristicas:
                if isinstance(carac, str):
                    q_objects &= Q(descripcion__icontains=carac.lower())
        
        productos = ProductoModel.objects.filter(q_objects).select_related(
            'marca', 'subcategoria', 'subcategoria__categoria'
        ).prefetch_related('imagenes')
        
        print(f"✅ Encontrados {productos.count()} productos potenciales")
        
        # Ordenar por mejor coincidencia
        if marca:
            productos = productos.order_by('-marca__nombre')
        
        return productos[:5]  # Máximo 5 productos más relevantes
