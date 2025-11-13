from rest_framework import viewsets
# reportes/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.http import HttpResponse
from django.conf import settings
from rest_framework.decorators import api_view
# endpoints_reportes_cliente.py
from rest_framework.decorators import action
from pydantic import BaseModel
from typing import List, Dict, Optional
import pandas as pd
from django.contrib.auth import get_user_model
import json
import os
import traceback
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta

import google.generativeai as genai

# --- Django ORM ---
from django.db import models
from django.db.models import Sum, Count, Q, Avg, Max, Min
from django.core.exceptions import FieldDoesNotExist
from django.utils import timezone

# --- Models de tu ecommerce ---
from usuario.models import Usuario, Grupo
from producto.models import ProductoModel, CategoriaModel, SubcategoriaModel, MarcaModel, CambioPrecioModel
from producto.serializers import ProductoSerializer
from venta.models import CarritoModel, DetalleCarritoModel, PedidoModel, DetallePedidoModel, FormaPagoModel, PlanPagoModel, PagoModel, MetodoPagoModel
from .serializers import UsuarioReporteSerializer, CarritoReporteSerializer, PedidoReporteSerializer, DetallePedidoReporteSerializer, PagoReporteSerializer, PlanPagoReporteSerializer, ProductoReporteSerializer, CategoriaReporteSerializer, MarcaReporteSerializer,VentasAgrupadasSerializer, PedidoClienteSerializer, DetallePedidoClienteSerializer
# from .permissions import IsAdminOrStaff
from .generators import generar_reporte_pdf, generar_reporte_excel
from utils.encrypted_logger import registrar_accion

# --- Configuración Gemini ---
try:
    from dateutil.parser import parse as dateutil_parse
except ImportError:
    dateutil_parse = None

try:
    from decouple import config as env_config
except Exception:
    env_config = None

# Configuración Gemini
GEMINI_CONFIGURED = False
GEMINI_MODEL_NAME = getattr(settings, 'GEMINI_MODEL_NAME', 'gemini-2.5-flash')

VALID_TIPOS = {
    'productos', 'categorias', 'marcas', 'carritos', 'pedidos', 
    'pagos', 'clientes', 'ventas', 'inventario', 'planes_pago'
}

DJANGO_LOOKUP_OPERATORS = [
    'exact', 'iexact', 'contains', 'icontains', 'in', 'gt', 'gte', 'lt', 'lte',
    'isnull', 'range', 'year', 'month', 'day', 'week_day', 'startswith',
    'istartswith', 'endswith', 'iendswith'
]

ALLOWED_AGGREGATIONS = {
    'Sum': Sum, 'Count': Count, 'Avg': Avg, 'Max': Max, 'Min': Min
}

MAX_ROWS = 1000

_GEMINI_API_KEY = getattr(settings, 'GEMINI_API_KEY', None) \
    or os.getenv('GEMINI_API_KEY') \
    or (env_config('GEMINI_API_KEY', default=None) if env_config else None)

if _GEMINI_API_KEY:
    try:
        genai.configure(api_key=_GEMINI_API_KEY)
        GEMINI_CONFIGURED = True
        print("[INFO] Gemini configurado correctamente para reportes.")
    except Exception as e:
        print(f"[ERROR] No se pudo configurar Gemini: {e}")
else:
    print("[WARN] GEMINI_API_KEY no encontrada. Reportes con IA deshabilitados.")

# --- Utilidades ---
def _json_converter(o):
    if isinstance(o, (datetime, date)): return o.isoformat()
    if isinstance(o, Decimal): return f"{o:.2f}"
    if isinstance(o, bool): return "Sí" if o else "No"
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

def _safe_decimal(value):
    try: return Decimal(value)
    except (InvalidOperation, TypeError, ValueError): return None

def _normalize_interpretacion(data: dict, default_tipo="productos"):
    if not isinstance(data, dict): data = {}
    tipo = str(data.get("tipo_reporte") or "").strip().lower()
    if tipo not in VALID_TIPOS: tipo = default_tipo
    out = {
        "tipo_reporte": tipo, "formato": "pantalla",
        "filtros": data.get("filtros") or {},
        "agrupacion": data.get("agrupacion") or [],
        "calculos": data.get("calculos") or {},
        "orden": data.get("orden") or [],
        "error": data.get("error") or None,
    }
    if not isinstance(out["filtros"], dict): out["filtros"] = {}
    if not isinstance(out["agrupacion"], list): out["agrupacion"] = []
    if not isinstance(out["calculos"], dict): out["calculos"] = {}
    if not isinstance(out["orden"], list): out["orden"] = []
    return out

def _naive_interpret(user_prompt: str):
    """Interpretación básica mejorada sin Gemini"""
    p = (user_prompt or "").lower()
    
    # Detección mejorada de marcas
    marcas_detectadas = []
    if "samsung" in p: marcas_detectadas.append("Samsung")
    if "lg" in p: marcas_detectadas.append("LG") 
    if "panasonic" in p: marcas_detectadas.append("Panasonic")
    if "mabe" in p: marcas_detectadas.append("Mabe")
    if "whirlpool" in p: marcas_detectadas.append("Whirlpool")
    
    tipo = "productos"
    if "pedido" in p or "venta" in p: 
        tipo = "pedidos"
    elif "producto" in p and ("vendido" in p or "más vendido" in p or "top" in p):
        tipo = "ventas"
    elif "pago" in p or "cuota" in p: 
        tipo = "pagos"
    elif "cliente" in p or "usuario" in p: 
        tipo = "clientes"
    elif "carrito" in p: 
        tipo = "carritos"
    elif "categoria" in p: 
        tipo = "categorias"
    elif "marca" in p: 
        tipo = "marcas"
    elif "inventario" in p or "stock" in p: 
        tipo = "inventario"
    
    filtros = {}
    if marcas_detectadas:
        # Usar filtro exacto para marcas
        filtros["marca__nombre__iexact"] = marcas_detectadas[0]
    
    if "activo" in p: 
        filtros["is_active"] = True
    if "inactivo" in p: 
        filtros["is_active"] = False
    if "stock bajo" in p: 
        filtros["stock__lt"] = 5
    if "sin stock" in p: 
        filtros["stock"] = 0
    if "pagado" in p: 
        filtros["estado__iexact"] = "pagado"
    if "pendiente" in p: 
        filtros["estado__iexact"] = "pendiente"
    
    # Para consultas de "más vendidos", usar ventas
    if tipo == "ventas":
        agrupacion = ["producto__id", "producto__nombre"]
        calculos = {"unidades_vendidas": "Sum('cantidad')"}
        orden = ["-unidades_vendidas"]
        
        # Agregar filtro de marca si se detectó
        if marcas_detectadas:
            filtros["producto__marca__nombre__iexact"] = marcas_detectadas[0]
            filtros["pedido__estado__iexact"] = "pagado"
    else:
        agrupacion = []
        calculos = {}
        orden = []
    
    return _normalize_interpretacion({
        "tipo_reporte": tipo, 
        "formato": "pantalla", 
        "filtros": filtros,
        "agrupacion": agrupacion, 
        "calculos": calculos, 
        "orden": orden, 
        "error": None,
    })

# ===================================================================
# ✅ CLASE BASE PARA REPORTES
# ===================================================================
class ReporteBaseView(APIView):
    # permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def _parse_date_value(self, value):
        if value is None: return None
        if isinstance(value, str):
            if dateutil_parse:
                try: return dateutil_parse(value)
                except ValueError: pass
            for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S'):
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt if ' ' in value else dt.date()
                except ValueError:
                    continue
            raise ValueError(f"Formato de fecha no reconocido: {value}. Use YYYY-MM-DD.")
        return value

    def _validate_and_convert_value(self, model_class, lookup, value):
        """Valida y convierte tipos de datos para filtros"""
        current_model = model_class
        field_instance = None
        parts = lookup.split('__')
        
        try:
            for i, part in enumerate(parts):
                is_last = (i == len(parts) - 1)
                if is_last and part in DJANGO_LOOKUP_OPERATORS:
                    break
                try:
                    field_instance = current_model._meta.get_field(part)
                    if getattr(field_instance, 'related_model', None):
                        current_model = field_instance.related_model
                    else:
                        if i < len(parts) - 1 and parts[i + 1] not in DJANGO_LOOKUP_OPERATORS:
                            raise FieldDoesNotExist(f"'{part}' no es relación válida en {current_model.__name__}")
                except FieldDoesNotExist as e:
                    raise FieldDoesNotExist(f"Campo/relación inválido en '{lookup}': {e}")

            if field_instance is None and parts[-1] not in DJANGO_LOOKUP_OPERATORS:
                raise FieldDoesNotExist(f"No se pudo resolver '{lookup}' en {model_class.__name__}")

            if value is None:
                if parts[-1] == 'isnull': return bool(value)
                return None

            lookup_operator = parts[-1] if parts[-1] in DJANGO_LOOKUP_OPERATORS else 'exact'
            converted_value = value

            if lookup_operator == 'isnull':
                converted_value = bool(value) if not isinstance(value, bool) else value
            elif lookup_operator == 'in':
                if not isinstance(value, list): raise ValueError("Valor para 'in' debe ser una lista.")
                converted_value = value
            elif lookup_operator == 'range':
                if not isinstance(value, list) or len(value) != 2: 
                    raise ValueError("Valor para 'range' debe ser lista de dos elementos [desde, hasta].")
                converted_value = [self._parse_date_value(value[0]), self._parse_date_value(value[1])]
            elif parts[-1] in ['year', 'month', 'day', 'week_day']:
                converted_value = int(value)
            elif field_instance:
                target_type = type(field_instance)
                if target_type in (models.DecimalField, models.FloatField):
                    dec = _safe_decimal(value)
                    if dec is None: raise ValueError(f"No se pudo convertir '{value}' a Decimal.")
                    converted_value = dec
                elif target_type == models.IntegerField:
                    converted_value = int(value)
                elif target_type in (models.DateTimeField, models.DateField, models.TimeField):
                    converted_value = self._parse_date_value(value)
                elif target_type == models.BooleanField:
                    if isinstance(value, str):
                        low = value.lower()
                        if low in ('true', '1', 't', 'yes', 'y', 'si', 'sí'): converted_value = True
                        elif low in ('false', '0', 'f', 'no', 'n'): converted_value = False
                        else: raise ValueError("Boolean string inválido.")
                    else:
                        converted_value = bool(value)
                elif target_type == models.CharField and not isinstance(value, str):
                    converted_value = str(value)

            return converted_value

        except FieldDoesNotExist as e:
            raise FieldDoesNotExist(f"Campo/relación inválido en '{lookup}': {e}")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Valor '{value}' inválido para filtro '{lookup}': {e}")

    def _get_serializer_class(self, tipo_reporte):
        """Obtiene el serializer class para el tipo de reporte"""
        serializer_map = {
            "productos": ProductoReporteSerializer,
            "categorias": CategoriaReporteSerializer,
            "marcas": MarcaReporteSerializer,
            "carritos": CarritoReporteSerializer,
            "pedidos": PedidoReporteSerializer,
            "pagos": PagoReporteSerializer,
            "clientes": UsuarioReporteSerializer,
            "ventas": DetallePedidoReporteSerializer,
            "inventario": ProductoReporteSerializer,  # Reutiliza el de productos
            "planes_pago": PlanPagoReporteSerializer,
            "ventas_agrupadas":VentasAgrupadasSerializer
        }
        return serializer_map.get(tipo_reporte)

    def _get_model_and_queryset(self, tipo_reporte):
        """Obtiene el modelo y queryset base para el tipo de reporte"""
        if tipo_reporte == "productos":
            return ProductoModel, ProductoModel.objects.select_related('marca', 'subcategoria', 'subcategoria__categoria')
        elif tipo_reporte == "categorias":
            return CategoriaModel, CategoriaModel.objects.all()
        elif tipo_reporte == "marcas":
            return MarcaModel, MarcaModel.objects.all()
        elif tipo_reporte == "carritos":
            return CarritoModel, CarritoModel.objects.select_related('usuario')
        elif tipo_reporte == "pedidos":
            return PedidoModel, PedidoModel.objects.select_related('usuario', 'forma_pago')
        elif tipo_reporte == "pagos":
            return PagoModel, PagoModel.objects.select_related('plan_pago', 'metodo_pago')
        elif tipo_reporte == "clientes":
            return Usuario, Usuario.objects.all()
        elif tipo_reporte == "ventas":  # 👈 MEJORADO para ventas/detalles
            return DetallePedidoModel, DetallePedidoModel.objects.select_related(
                'pedido', 'pedido__usuario', 'producto', 'producto__marca'
            )
        elif tipo_reporte == "inventario":
            return ProductoModel, ProductoModel.objects.select_related('marca', 'subcategoria')
        elif tipo_reporte == "planes_pago":
            return PlanPagoModel, PlanPagoModel.objects.select_related('pedido')
        else:
            raise ValueError(f"Tipo de reporte '{tipo_reporte}' no soportado.")

    def _build_queryset(self, interpretacion):
        """Construye el queryset según la interpretación"""
        tipo = interpretacion.get("tipo_reporte")
        filtros_dict = interpretacion.get("filtros", {})
        agrupacion_list = interpretacion.get("agrupacion", [])
        calculos_dict = interpretacion.get("calculos", {})
        orden_list = interpretacion.get("orden", [])
        limite = interpretacion.get("limite")

        print(f"🎯 Interpretación recibida:")
        print(f"   Tipo: {tipo}")
        print(f"   Filtros originales: {filtros_dict}")

        # 👈 CORREGIR FILTROS DE MARCA AUTOMÁTICAMENTE
        filtros_dict = self._corregir_filtros_por_tipo(tipo,filtros_dict)
        print(f"   Filtros corregidos: {filtros_dict}")
        # Obtener modelo y queryset base
        ModelClass, base_queryset = self._get_model_and_queryset(tipo)

        # Obtener modelo y queryset base
        ModelClass, base_queryset = self._get_model_and_queryset(tipo)

        # Aplicar filtros
        q_filtros = Q()
        for lookup, value in dict(filtros_dict).items():
            try:
                # Manejar fechas relativas (si las tienes)
                if isinstance(value, str) and value.startswith("RELATIVE:"):
                    if value == "RELATIVE:LAST_MONTH":
                        first_day = timezone.now().replace(day=1) - timedelta(days=1)
                        first_day = first_day.replace(day=1)
                        last_day = timezone.now().replace(day=1) - timedelta(days=1)
                        value = [first_day, last_day]
                    elif value == "RELATIVE:LAST_30_DAYS":
                        value = [timezone.now() - timedelta(days=30), timezone.now()]
                
                converted_value = self._validate_and_convert_value(ModelClass, lookup, value)
                q_filtros &= Q(**{lookup: converted_value})
                print(f"   ✅ Filtro aplicado: {lookup} = {converted_value}")
            except (FieldDoesNotExist, ValueError, TypeError) as e:
                print(f"[WARN] Skipping invalid filter: {lookup}={repr(value)}. Reason: {e}")
                print(f"   ❌ Filtro ignorado: {lookup}={repr(value)}. Razón: {e}")
                continue

        queryset = base_queryset.filter(q_filtros)

        # Agrupación
        hubo_agrupacion = False
        valid_agrupacion = []
        if agrupacion_list:
            hubo_agrupacion = True
            for field_path in agrupacion_list:
                try:
                    self._validate_and_convert_value(ModelClass, field_path, None)
                    valid_agrupacion.append(field_path)
                except (FieldDoesNotExist, ValueError):
                    print(f"[WARN] Invalid grouping field skipped: {field_path}")
            if not valid_agrupacion:
                raise ValueError("Ningún campo de agrupación válido.")
            queryset = queryset.values(*valid_agrupacion)

            # Cálculos
            if calculos_dict:
                aggregations = {}
                for name, expr in calculos_dict.items():
                    agg_func_name, field_in_agg_raw = None, None
                    if isinstance(expr, str):
                        parts = expr.replace(")", "").split("(")
                        if len(parts) == 2:
                            agg_func_name, field_in_agg_raw = parts
                    elif isinstance(expr, dict):
                        agg_func_name, field_in_agg_raw = expr.get("funcion"), expr.get("campo")
                    else:
                        print(f"[WARN] Valor de cálculo desconocido: {expr}")
                        continue

                    if agg_func_name and field_in_agg_raw:
                        field_in_agg = field_in_agg_raw.strip("'\" ")
                        if agg_func_name in ALLOWED_AGGREGATIONS and field_in_agg:
                            AggFunc = ALLOWED_AGGREGATIONS[agg_func_name]
                            try:
                                validation_field = field_in_agg if field_in_agg != '*' else 'id'
                                self._validate_and_convert_value(ModelClass, validation_field, None)
                                aggregations[name] = AggFunc(field_in_agg)
                            except (FieldDoesNotExist, ValueError, TypeError):
                                print(f"[WARN] Invalid field in aggregation skipped: {field_in_agg}")
                        else:
                            print(f"[WARN] Invalid aggregation function skipped: {agg_func_name}")
                    else:
                        print(f"[WARN] Could not parse aggregation: {expr}")

                if aggregations:
                    queryset = queryset.annotate(**aggregations)

            if not orden_list:
                orden_list = valid_agrupacion

        # Ordenamiento
        final_orden_fields = []
        if orden_list:
            for field_order in orden_list:
                field_name = field_order.lstrip('-')
                is_group_field = field_name in valid_agrupacion
                is_calc_field = field_name in calculos_dict
                is_model_field = not hubo_agrupacion
                if is_model_field:
                    try:
                        self._validate_and_convert_value(ModelClass, field_name, None)
                    except (FieldDoesNotExist, ValueError):
                        is_model_field = False
                if is_group_field or is_calc_field or is_model_field:
                    final_orden_fields.append(field_order)
                else:
                    print(f"[WARN] Invalid ordering field skipped: {field_order}")

        if final_orden_fields:
            queryset = queryset.order_by(*final_orden_fields)
        elif not hubo_agrupacion:
            # Orden por defecto según tipo
            orden_por_defecto = {
                "productos": ['-fecha_registro'],
                "pedidos": ['-fecha'],
                "pagos": ['-fecha_pago'],
                "carritos": ['-fecha'],
                "clientes": ['-date_joined'],
                "categorias": ['nombre'],
                "marcas": ['nombre'],
                "ventas": ['-pedido__fecha'],
                "inventario": ['-stock'],
                "planes_pago": ['-fecha_vencimiento'],
            }
            orden = orden_por_defecto.get(tipo, ['-id'])
            queryset = queryset.order_by(*orden)

        # 👈 APLICAR LÍMITE SI EXISTE
        if limite and isinstance(limite, int) and limite > 0:
            queryset = queryset[:limite]
        else:
            queryset = queryset[:MAX_ROWS]  # Límite por defecto

        return queryset, hubo_agrupacion
    
    # En ReporteBaseView, actualiza el método _corregir_filtros_marca
    def _corregir_filtros_por_tipo(self, tipo_reporte, filtros_dict):
        """Corrige automáticamente los filtros según el tipo de reporte"""
        filtros_corregidos = {}
        
        for lookup, value in filtros_dict.items():
            # Para reportes de "ventas" (DetallePedidoModel), ajustar los filtros
            if tipo_reporte == "ventas":
                # Si el filtro es de marca pero no está prefijado con producto__
                if lookup in ['marca__nombre__iexact', 'marca__nombre__icontains']:
                    nuevo_lookup = f"producto__{lookup}"
                    filtros_corregidos[nuevo_lookup] = value
                    print(f"🔧 Corrección ventas: {lookup} → {nuevo_lookup}")
                # Si el filtro es de categoría pero no está prefijado con producto__
                elif lookup in ['categoria__nombre__iexact', 'categoria__nombre__icontains', 
                            'subcategoria__nombre__iexact', 'subcategoria__nombre__icontains']:
                    nuevo_lookup = f"producto__{lookup}"
                    filtros_corregidos[nuevo_lookup] = value
                    print(f"🔧 Corrección ventas: {lookup} → {nuevo_lookup}")
                # Si ya está bien prefijado o es un filtro válido para DetallePedidoModel
                elif (lookup.startswith('producto__') or 
                    lookup.startswith('pedido__') or
                    lookup in ['cantidad', 'precio_unitario', 'subtotal'] or
                    any(op in lookup for op in DJANGO_LOOKUP_OPERATORS)):
                    filtros_corregidos[lookup] = value
                else:
                    print(f"⚠️  Filtro ignorado para ventas: {lookup} (no válido para DetallePedidoModel)")
            
            # Para otros tipos de reporte, mantener la corrección de marcas
            else:
                if lookup in ['producto__marca__nombre__icontains', 'marca__nombre__icontains']:
                    nuevo_lookup = lookup.replace('__icontains', '__iexact')
                    filtros_corregidos[nuevo_lookup] = value
                    print(f"🔧 Corrección marca: {lookup} → {nuevo_lookup}")
                else:
                    filtros_corregidos[lookup] = value
        
        return filtros_corregidos
    def _serializar_datos(self, queryset, tipo_reporte, hubo_agrupacion):
        """Serializa los datos usando los serializers específicos"""
        if hubo_agrupacion:
            # Para datos agrupados, usar values() directamente
            datos = list(queryset)

            # Mejorar datos agrupados con nombres legibles
            for item in datos:
                if 'producto__id' in item and 'producto__nombre' in item:
                    item['producto_id'] = item.pop('producto__id')
                    item['producto_nombre'] = item.pop('producto__nombre')
                if 'pedido__usuario__username' in item:
                    item['cliente'] = item.pop('pedido__usuario__username')

                # CONVERTIR campos numéricos a tipos correctos
                for key, value in item.items():
                    if isinstance(value, Decimal):
                        item[key] = float(value)
                    elif key.endswith('_id') and value is not None:
                        try:
                            item[key] = int(value)
                        except (ValueError, TypeError):
                            pass
                        
            return datos

        # Para datos no agrupados, usar serializers
        serializer_class = self._get_serializer_class(tipo_reporte)

        if serializer_class:
            # Usar serializer para datos estructurados y seguros
            serializer = serializer_class(queryset, many=True)
            data = serializer.data

            # Asegurar que los campos numéricos sean del tipo correcto
            for item in data:
                for key, value in item.items():
                    if isinstance(value, Decimal):
                        item[key] = float(value)
                    elif key in ['total', 'precio_contado', 'precio_cuota', 'monto', 'subtotal', 'precio_unitario'] and value is not None:
                        try:
                            item[key] = float(value)
                        except (ValueError, TypeError):
                            pass
                        
            return data
        else:
            # Fallback para tipos sin serializer específico
            campos_seguros = {
                "productos": ['id', 'nombre', 'marca__nombre', 'precio_contado', 'stock', 'is_active'],
                "clientes": ['id', 'username', 'first_name', 'last_name', 'email', 'ci', 'telefono', 'date_joined', 'is_active'],
                "pedidos": ['id', 'usuario__username', 'total', 'estado', 'fecha', 'is_active'],
                "pagos": ['id', 'monto', 'fecha_pago', 'metodo_pago__nombre', 'is_active'],
                "carritos": ['id', 'usuario__username', 'total', 'fecha', 'is_active'],
                "ventas": ['id', 'producto__nombre', 'cantidad', 'precio_unitario', 'subtotal', 'pedido__fecha'],
                "categorias": ['id', 'nombre', 'descripcion', 'is_active'],
                "marcas": ['id', 'nombre', 'descripcion', 'is_active'],
                "planes_pago": ['id', 'usuario__username', 'monto', 'fecha_vencimiento', 'is_active'],
                "inventario": ['id', 'producto__nombre', 'stock', 'is_active'],
            }

            campos = campos_seguros.get(tipo_reporte, [])
            if campos:
                datos = list(queryset.values(*campos))

                # CONVERTIR campos numéricos
                for item in datos:
                    for key, value in item.items():
                        if isinstance(value, Decimal):
                            item[key] = float(value)
                        elif key in ['total', 'precio_contado', 'precio_cuota', 'monto', 'subtotal', 'precio_unitario', 'stock'] and value is not None:
                            try:
                                item[key] = float(value)
                            except (ValueError, TypeError):
                                pass
                            
                return datos
            else:
                datos = list(queryset.values())

                # CONVERTIR campos numéricos
                for item in datos:
                    for key, value in item.items():
                        if isinstance(value, Decimal):
                            item[key] = float(value)
                        elif key in ['total', 'precio_contado', 'precio_cuota', 'monto', 'subtotal', 'precio_unitario', 'stock'] and value is not None:
                            try:
                                item[key] = float(value)
                            except (ValueError, TypeError):
                                pass
                            
                return datos

# ===================================================================
# VISTA #1: GenerarReporteView (CON IA)
# ===================================================================
class GenerarReporteView(ReporteBaseView):

    def _call_gemini_api(self, user_prompt: str):
        if not GEMINI_CONFIGURED:
            return _naive_interpret(user_prompt)

        now = timezone.now()
        current_date_str = now.strftime('%Y-%m-%d')

        # SCHEMA MEJORADO con relaciones y consultas comunes
        schema_definition = f"""
    ESQUEMA MEJORADO - ECOMMERCE (Moneda: Bs.)
    Fecha actual: {current_date_str}

    MODELOS PRINCIPALES:

    1. PRODUCTOS (ProductoModel):
    - Campos: id, nombre, descripcion, modelo, precio_contado, precio_cuota, stock, garantia_meses, fecha_registro, is_active
    - Relaciones: marca->MarcaModel, subcategoria->SubcategoriaModel->CategoriaModel

    2. PEDIDOS (PedidoModel):
    - Campos: id, fecha, total, estado ('pendiente','pagando','pagado','cancelado'), is_active
    - Relaciones: usuario->Usuario, forma_pago->FormaPagoModel

    3. DETALLES_PEDIDO (DetallePedidoModel) - PARA VENTAS:
    - Campos: id, cantidad, precio_unitario, subtotal, is_active
    - Relaciones: pedido->PedidoModel, producto->ProductoModel
    - IMPORTANTE: Para productos más vendidos usar este modelo

    4. CLIENTES (Usuario):
    - Campos: id, username, first_name, last_name, email, ci, telefono, date_joined, is_active
    - Relaciones: pedido_set (lista de pedidos del cliente)

    5. PAGOS (PagoModel):
    - Campos: id, fecha_pago, monto, comprobante, is_active
    - Relaciones: plan_pago->PlanPagoModel, metodo_pago->MetodoPagoModel

    CONSULTAS COMUNES PRE-DEFINIDAS:

    A) PRODUCTOS MÁS VENDIDOS:
    - tipo_reporte: "ventas" (DetallePedidoModel)
    - agrupacion: ["producto__id", "producto__nombre"]
    - calculos: {{"total_vendido": "Sum('cantidad')", "ingresos_totales": "Sum('subtotal')"}}
    - orden: ["-total_vendido"]
    - filtros: {{"pedido__estado": "pagado"}}

    B) CLIENTES CON SUS PEDIDOS:
    - tipo_reporte: "clientes"
    - NO usar agrupacion (para datos detallados)
    - Usar serializer que incluya pedidos relacionados

    C) VENTAS POR CATEGORÍA:
    - tipo_reporte: "ventas" 
    - agrupacion: ["producto__subcategoria__categoria__nombre"]
    - calculos: {{"total_ventas": "Sum('subtotal')", "unidades_vendidas": "Sum('cantidad')"}}

    D) PRODUCTOS CON BAJO STOCK:
    - tipo_reporte: "productos"
    - filtros: {{"stock__lt": 10, "is_active": true}}
    - orden: ["stock"]

    E) PEDIDOS RECIENTES CON DETALLES:
    - tipo_reporte: "pedidos"
    - orden: ["-fecha"]
    - filtros: {{"estado": "pagado"}}

    REGLAS ESTRICTAS:
    1. Para "más vendidos" SIEMPRE usar tipo_reporte: "ventas" (DetallePedidoModel)
    2. Para límites usar [:limit] en Python, NO en la consulta SQL
    3. Para relaciones usar doble guión bajo: "producto__marca__nombre"
    4. Para cálculos de ventas usar siempre "pedido__estado": "pagado"
    """
        
        system_instruction = f"""
Eres un experto en bases de datos SQL y Django ORM para Ecommerce. Fecha: {current_date_str}.

ANALIZA la consulta del usuario y DEVUELVE JSON con esta estructura:

{{
  "tipo_reporte": "string", 
  "formato": "pantalla",
  "filtros": {{ "campo__lookup": "valor" }},
  "agrupacion": ["campo"], 
  "calculos": {{ "nombre": "Funcion('campo')" }}, 
  "orden": ["campo"],
  "limite": número,
  "error": null
}}

REGLAS CRÍTICAS DE FILTRADO:

1. PARA MARCAS ESPECÍFICAS usar EXACTAMENTE:
   - "Samsung" → "producto__marca__nombre__iexact": "Samsung"
   - "LG" → "producto__marca__nombre__iexact": "LG" 
   - "Panasonic" → "producto__marca__nombre__iexact": "Panasonic"
   - NO usar "icontains" para marcas específicas

2. PARA BÚSQUEDAS GENERALES usar:
   - "productos de refrigeración" → "producto__subcategoria__categoria__nombre__icontains": "refrigeración"
   - "productos con pantalla" → "producto__nombre__icontains": "pantalla"

3. FILTROS COMUNES EXACTOS:
   - Solo activos: "is_active": true
   - Solo pedidos pagados: "pedido__estado__iexact": "pagado"
   - Stock mayor a cero: "producto__stock__gt": 0

EJEMPLOS CORREGIDOS:

Usuario: "productos samsung más vendidos"
Respuesta: {{
  "tipo_reporte": "ventas",
  "filtros": {{
    "pedido__estado__iexact": "pagado",
    "producto__marca__nombre__iexact": "Samsung",
    "producto__is_active": true
  }},
  "agrupacion": ["producto__id", "producto__nombre"],
  "calculos": {{
    "unidades_vendidas": "Sum('cantidad')",
    "ingresos_totales": "Sum('subtotal')"
  }},
  "orden": ["-unidades_vendidas"]
}}

Usuario: "refrigeradores lg"
Respuesta: {{
  "tipo_reporte": "productos",
  "filtros": {{
    "producto__marca__nombre__iexact": "LG",
    "subcategoria__nombre__icontains": "refrigerador",
    "is_active": true
  }},
  "orden": ["nombre"]
}}

Usuario: "top 5 marcas más vendidas"
Respuesta: {{
  "tipo_reporte": "ventas",
  "filtros": {{
    "pedido__estado__iexact": "pagado"
  }},
  "agrupacion": ["producto__marca__nombre"],
  "calculos": {{
    "total_ventas": "Sum('subtotal')",
    "unidades_vendidas": "Sum('cantidad')"
  }},
  "orden": ["-total_ventas"],
  "limite": 5
}}

IMPORTANTE: Para consultas de marca específica, usar SIEMPRE __iexact no __icontains.
"""
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            generation_config = genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1  # Menos creatividad, más precisión
            )
            response = model.generate_content(
                [system_instruction, schema_definition, user_prompt],
                generation_config=generation_config
            )

            raw_response_text = (response.text or "").strip()
            print(f"[Gemini] Raw JSON response:\n{raw_response_text}")

            cleaned = raw_response_text.removeprefix("```json").removesuffix("```").strip()
            if not (cleaned.startswith('{') and cleaned.endswith('}')):
                i, j = cleaned.find('{'), cleaned.rfind('}')
                if i != -1 and j != -1 and j > i:
                    cleaned = cleaned[i:j+1]
                else:
                    raise json.JSONDecodeError("No JSON object found", cleaned, 0)

            parsed = json.loads(cleaned)
            interp = _normalize_interpretacion(parsed, default_tipo="productos")
            
            # Agregar límite si viene en la respuesta
            if 'limite' in parsed and isinstance(parsed['limite'], int):
                interp['limite'] = parsed['limite']

            if parsed.get("error") and interp["tipo_reporte"] in VALID_TIPOS:
                print(f"[Gemini] Warning from LLM: {parsed.get('error')}. Using tolerant mode.")
                interp["error"] = None
            
            return interp

        except Exception as e:
            print(f"[ERROR] Gemini failed -> falling back to naive. Reason: {e}")
            traceback.print_exc()
            return _naive_interpret(user_prompt)

    def post(self, request, *args, **kwargs):
        prompt = (request.data.get('prompt') or "").strip()
        if not prompt:
            interpretacion = _naive_interpret(prompt)
        else:
            interpretacion = self._call_gemini_api(prompt)

        interpretacion["error"] = None
        interpretacion["prompt"] = prompt

        try:
            queryset, hubo_agrupacion = self._build_queryset(interpretacion)
        except ValueError as e:
            print(f"[WARN] Build queryset failed: {e}. Falling back to simple list.")
            interpretacion = _normalize_interpretacion({}, default_tipo="productos")
            queryset, hubo_agrupacion = self._build_queryset(interpretacion)
        except Exception as e:
            print(f"[ERROR] Unexpected error building queryset: {e}")
            traceback.print_exc()
            return Response({"error": "Error interno al procesar."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            tipo_reporte = interpretacion.get("tipo_reporte")
            
            # ✅ USAR EL NUEVO MÉTODO DE SERIALIZACIÓN
            data_para_reporte = self._serializar_datos(queryset, tipo_reporte, hubo_agrupacion)

            json_output = json.dumps(data_para_reporte, default=_json_converter)
            registrar_accion(request.user, f"Genero reporte de {tipo_reporte}", request.META.get('REMOTE_ADDR'))
            return HttpResponse(json_output, content_type='application/json', status=status.HTTP_200_OK)

        except Exception as e:
            print(f"[ERROR] Exception during data preparation: {e}")
            traceback.print_exc()
            return Response({"error": "Error al preparar los datos del reporte."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ===================================================================
# VISTA #2: ReporteDirectoView (SIN IA)
# ===================================================================
class ReporteDirectoView(ReporteBaseView):
    """
    Reportes directos con filtros predefinidos
    """

    def post(self, request, *args, **kwargs):
        builder_data = request.data
        print(f"[Direct Report] Received builder data: {builder_data}")

        try:
            interpretacion = self._traducir_builder_a_interpretacion(builder_data)
        except Exception as e:
            print(f"[ERROR] Error traduciendo el builder JSON: {e}")
            return Response({"error": f"Error en el formato del builder: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            queryset, hubo_agrupacion = self._build_queryset(interpretacion)
        except ValueError as e:
            return Response({"error": f"Error al procesar solicitud: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(f"[ERROR] Unexpected error building queryset: {e}")
            traceback.print_exc()
            return Response({"error": "Error interno al procesar."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            tipo_reporte = interpretacion.get("tipo_reporte")
            
            # ✅ USAR EL NUEVO MÉTODO DE SERIALIZACIÓN
            data_para_reporte = self._serializar_datos(queryset, tipo_reporte, hubo_agrupacion)

            json_output = json.dumps(data_para_reporte, default=_json_converter)
            registrar_accion(request.user, f"Genero reporte de {tipo_reporte}", request.META.get('REMOTE_ADDR'))
            return HttpResponse(json_output, content_type='application/json', status=status.HTTP_200_OK)

        except Exception as e:
            print(f"[ERROR] Exception during data preparation: {e}")
            traceback.print_exc()
            return Response({"error": "Error al preparar los datos del reporte."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _traducir_builder_a_interpretacion(self, builder):
        """Convierte JSON simple al formato de interpretación"""
        tipo = (builder or {}).get("tipo", "productos")
        filtros_in = (builder or {}).get("filtros", {})

        filtros_out = {}

        # Estado
        if filtros_in.get("estado"):
            if tipo == "productos":
                filtros_out["is_active"] = filtros_in["estado"] == "activo"
            elif tipo == "pedidos":
                filtros_out["estado__exact"] = filtros_in["estado"]
            elif tipo == "pagos":
                # Para pagos, el estado está en plan_pago
                filtros_out["plan_pago__estado__exact"] = filtros_in["estado"]
            elif tipo == "carritos":
                filtros_out["is_active"] = filtros_in["estado"] == "activo"
            elif tipo == "clientes":
                filtros_out["is_active"] = filtros_in["estado"] == "activo"
            elif tipo == "planes_pago":
                filtros_out["estado__exact"] = filtros_in["estado"]

        # Fechas
        fecha_desde = filtros_in.get("fechaDesde")
        fecha_hasta = filtros_in.get("fechaHasta")

        DATE_FIELD_MAP = {
            "productos": "fecha_registro",
            "pedidos": "fecha",
            "pagos": "fecha_pago",
            "carritos": "fecha",
            "clientes": "date_joined",
            "planes_pago": "fecha_vencimiento",
            "ventas": "pedido__fecha",
        }
        date_field = DATE_FIELD_MAP.get(tipo, "id")

        if fecha_desde and fecha_hasta:
            filtros_out[f"{date_field}__range"] = [fecha_desde, fecha_hasta]
        elif fecha_desde:
            filtros_out[f"{date_field}__gte"] = fecha_desde
        elif fecha_hasta:
            filtros_out[f"{date_field}__lte"] = fecha_hasta

        # Stock (para productos e inventario)
        if tipo in ["productos", "inventario"] and filtros_in.get("stockOp"):
            stock_valor = filtros_in.get("stockValor")
            if stock_valor is not None:
                filtros_out[f"stock__{filtros_in['stockOp']}"] = stock_valor

        # Precio (para productos)
        if tipo == "productos" and filtros_in.get("precioOp"):
            precio_valor = filtros_in.get("precioValor")
            if precio_valor is not None:
                filtros_out[f"precio_contado__{filtros_in['precioOp']}"] = precio_valor

        # Monto (para pedidos)
        if tipo == "pedidos" and filtros_in.get("montoOp"):
            monto_valor = filtros_in.get("montoValor")
            if monto_valor is not None:
                filtros_out[f"total__{filtros_in['montoOp']}"] = monto_valor

        # Monto (para pagos)
        if tipo == "pagos" and filtros_in.get("montoOp"):
            monto_valor = filtros_in.get("montoValor")
            if monto_valor is not None:
                filtros_out[f"monto__{filtros_in['montoOp']}"] = monto_valor

        # Monto (para planes_pago)
        if tipo == "planes_pago" and filtros_in.get("montoOp"):
            monto_valor = filtros_in.get("montoValor")
            if monto_valor is not None:
                filtros_out[f"monto__{filtros_in['montoOp']}"] = monto_valor

        # Búsqueda por texto (para productos y clientes)
        if filtros_in.get("busqueda"):
            if tipo == "productos":
                filtros_out["nombre__icontains"] = filtros_in["busqueda"]
            elif tipo == "clientes":
                filtros_out["username__icontains"] = filtros_in["busqueda"]
        interpretacion = {
            "tipo_reporte": tipo,
            "formato": "pantalla",
            "filtros": filtros_out,
            "agrupacion": [],
            "calculos": {},
            "orden": [],
            "error": None
        }

        print(f"[Direct Report] Interpretacion generada: {interpretacion}")
        return interpretacion

# ===================================================================
# VISTA #3: ExportarDatosView
# ===================================================================
class ExportarDatosView(ReporteBaseView):

    def post(self, request, *args, **kwargs):
        data = request.data.get('data')
        formato = (request.data.get('formato') or "").lower()
        prompt = request.data.get('prompt', 'Reporte Ecommerce')

        if not data or not isinstance(data, list):
            return Response({"error": "No se proporcionaron datos válidos para exportar."},
                            status=status.HTTP_400_BAD_REQUEST)
        if formato not in ['pdf', 'excel']:
            return Response({"error": "Formato no válido. Debe ser 'pdf' o 'excel'."},
                            status=status.HTTP_400_BAD_REQUEST)

        interpretacion = {'prompt': prompt, 'formato': formato}
        print(f"[Export] Solicitud de exportación. Formato: {formato}. Filas: {len(data)}")
        
        try:
            if formato == "pdf":
                return generar_reporte_pdf(data, interpretacion)
            else:
                return generar_reporte_excel(data, interpretacion)
        except Exception as e:
            print(f"[ERROR] Falló la generación del archivo: {e}")
            traceback.print_exc()
            return Response({"error": "Error interno al generar el archivo."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        


def _normalize_interpretacion(parsed, default_tipo="pedidos"):
    """Normaliza la interpretación de Gemini"""
    return {
        "tipo_reporte": parsed.get("tipo_reporte", default_tipo),
        "formato": parsed.get("formato", "pantalla"),
        "filtros": parsed.get("filtros", {}),
        "agrupacion": parsed.get("agrupacion", []),
        "calculos": parsed.get("calculos", {}),
        "orden": parsed.get("orden", []),
        "limite": parsed.get("limite", 20),
        "error": parsed.get("error")
    }

def _limpiar_datos_para_json(datos):
    """Convierte Decimal a float y maneja otros tipos no serializables"""
    if isinstance(datos, dict):
        return {k: _limpiar_datos_para_json(v) for k, v in datos.items()}
    elif isinstance(datos, list):
        return [_limpiar_datos_para_json(item) for item in datos]
    elif isinstance(datos, Decimal):
        return float(datos)
    elif isinstance(datos, (datetime, date)):
        return datos.isoformat()
    elif hasattr(datos, 'isoformat'):
        return datos.isoformat()
    else:
        return datos

def _convertir_tipos_numericos(datos):
    """Convierte campos numéricos a los tipos correctos"""
    if isinstance(datos, dict):
        for key, value in datos.items():
            if isinstance(value, (Decimal, str)):
                if key in ['total', 'precio_contado', 'precio_cuota', 'monto', 'subtotal', 'precio_unitario', 'stock', 'cantidad']:
                    try:
                        if isinstance(value, Decimal):
                            datos[key] = float(value)
                        elif isinstance(value, str):
                            datos[key] = float(value)
                    except (ValueError, TypeError):
                        pass
                elif key.endswith('_id') and value is not None:
                    try:
                        datos[key] = int(value)
                    except (ValueError, TypeError):
                        pass
    elif isinstance(datos, list):
        for item in datos:
            _convertir_tipos_numericos(item)
    return datos

def _obtener_datos_cliente(cliente):
    """Obtiene datos estructurados del cliente para IA"""
    try:
        pedidos_cliente = PedidoModel.objects.filter(usuario=cliente)
        total_pedidos = pedidos_cliente.count()
        total_gastado = pedidos_cliente.aggregate(total=Sum('total'))['total'] or 0

        productos_frecuentes = DetallePedidoModel.objects.filter(
            pedido__usuario=cliente
        ).values(
            'producto__nombre', 
            'producto__marca__nombre'
        ).annotate(
            veces_comprado=Count('id'),
            total_unidades=Sum('cantidad'),
            total_gastado=Sum('subtotal')
        ).order_by('-veces_comprado')[:3]

        productos_frecuentes_limpios = []
        for producto in productos_frecuentes:
            producto_limpio = {}
            for key, value in producto.items():
                if isinstance(value, Decimal):
                    producto_limpio[key] = float(value)
                else:
                    producto_limpio[key] = value
            productos_frecuentes_limpios.append(producto_limpio)

        ultimo_pedido = pedidos_cliente.order_by('-fecha').first()
        ultimo_pedido_data = {}
        if ultimo_pedido:
            ultimo_pedido_data = {
                "fecha": ultimo_pedido.fecha.strftime('%Y-%m-%d'),
                "total": float(ultimo_pedido.total),
                "estado": ultimo_pedido.estado
            }

        pedidos_por_estado = list(pedidos_cliente.values('estado').annotate(
            total=Count('id')
        ))

        seis_meses_atras = timezone.now() - timedelta(days=180)
        tendencia_mensual = list(pedidos_cliente.filter(
            fecha__gte=seis_meses_atras
        ).extra(
            {'mes': "DATE_TRUNC('month', fecha)"}
        ).values('mes').annotate(
            total_mes=Sum('total'),
            pedidos_mes=Count('id')
        ).order_by('mes'))

        for item in tendencia_mensual:
            if 'total_mes' in item and isinstance(item['total_mes'], Decimal):
                item['total_mes'] = float(item['total_mes'])

        return {
            "nombre_cliente": cliente.get_full_name() or cliente.username,
            "total_pedidos": total_pedidos,
            "total_gastado": float(total_gastado),
            "promedio_por_pedido": float(total_gastado / total_pedidos) if total_pedidos > 0 else 0,
            "productos_frecuentes": productos_frecuentes_limpios,
            "ultimo_pedido": ultimo_pedido_data,
            "pedidos_por_estado": pedidos_por_estado,
            "tendencia_mensual": tendencia_mensual,
            "miembro_desde": cliente.date_joined.strftime('%Y-%m-%d'),
            "meses_como_cliente": (timezone.now().date() - cliente.date_joined.date()).days // 30
        }

    except Exception as e:
        print(f"[ERROR] Obteniendo datos cliente: {e}")
        return {}

def _build_queryset(interpretacion):
    """Construye el queryset basado en la interpretación - MEJORADO para tus modelos"""
    from django.db.models import Count, Sum
    
    tipo_reporte = interpretacion.get("tipo_reporte")
    filtros = interpretacion.get("filtros", {})
    agrupacion = interpretacion.get("agrupacion", [])
    calculos = interpretacion.get("calculos", {})
    orden = interpretacion.get("orden", [])
    limite = interpretacion.get("limite", 20)
    
    hubo_agrupacion = bool(agrupacion)
    
    print(f"[DEBUG] Construyendo queryset - Tipo: {tipo_reporte}")
    print(f"[DEBUG] Filtros aplicados: {filtros}")
    
    try:
        if tipo_reporte == "pedidos":
            # Validar que el filtro de seguridad esté presente
            if "usuario__id" not in filtros:
                print("[SEGURIDAD] Faltaba filtro usuario__id, agregando...")
                filtros["usuario__id"] = interpretacion.get("filtros", {}).get("usuario__id", "unknown")
            
            queryset = PedidoModel.objects.filter(**filtros)
            
            if agrupacion:
                queryset = queryset.values(*agrupacion)
                annotations = {}
                for key, value in calculos.items():
                    if 'Count' in value:
                        annotations[key] = Count('id')
                    elif 'Sum' in value:
                        field = value.split("'")[1] if "'" in value else 'total'
                        annotations[key] = Sum(field)
                if annotations:
                    queryset = queryset.annotate(**annotations)
            
            if orden:
                queryset = queryset.order_by(*orden)
            
            if limite:
                queryset = queryset[:limite]
                
        elif tipo_reporte == "ventas":
            # Validar que el filtro de seguridad esté presente
            if "pedido__usuario__id" not in filtros:
                print("[SEGURIDAD] Faltaba filtro pedido__usuario__id, agregando...")
                filtros["pedido__usuario__id"] = interpretacion.get("filtros", {}).get("pedido__usuario__id", "unknown")
            
            queryset = DetallePedidoModel.objects.filter(**filtros)
            
            if agrupacion:
                queryset = queryset.values(*agrupacion)
                annotations = {}
                for key, value in calculos.items():
                    if 'Count' in value:
                        annotations[key] = Count('id')
                    elif 'Sum' in value:
                        field = value.split("'")[1] if "'" in value else 'subtotal'
                        annotations[key] = Sum(field)
                if annotations:
                    queryset = queryset.annotate(**annotations)
            
            if orden:
                queryset = queryset.order_by(*orden)
            
            if limite:
                queryset = queryset[:limite]
                
        else:
            queryset = PedidoModel.objects.none()
            
        print(f"[DEBUG] Queryset exitoso: {queryset.count()} registros")
        return queryset, hubo_agrupacion
        
    except Exception as e:
        print(f"[ERROR] Build queryset: {e}")
        print(f"[DEBUG] Filtros problemáticos: {filtros}")
        
        # FALLBACK SEGURO: retornar solo con filtros básicos de seguridad
        try:
            if tipo_reporte == "pedidos":
                queryset = PedidoModel.objects.filter(usuario__id=datos_cliente.get('id'))
            elif tipo_reporte == "ventas":
                queryset = DetallePedidoModel.objects.filter(pedido__usuario__id=datos_cliente.get('id'))
            else:
                queryset = PedidoModel.objects.none()
            return queryset, False
        except Exception as fallback_error:
            print(f"[ERROR] Fallback también falló: {fallback_error}")
            return PedidoModel.objects.none(), False
          
def _serializar_datos(queryset, tipo_reporte, hubo_agrupacion):
    """Serializa los datos según el tipo de reporte"""
    if hubo_agrupacion:
        return list(queryset)
    
    if tipo_reporte == "pedidos":
        serializer = PedidoClienteSerializer(queryset, many=True)
        return serializer.data
    elif tipo_reporte == "ventas":
        return list(queryset.values())
    
    return []

# En tu views.py, corrige la función _call_gemini_cliente

# En tu views.py - FUNCIONES CORREGIDAS

def _call_gemini_cliente(user_prompt: str, datos_cliente: dict):
    """Gemini especializado para consultas del cliente - CORREGIDO para tus modelos"""
    if not GEMINI_CONFIGURED:
        return _naive_interpret_cliente(user_prompt, datos_cliente)

    now = timezone.now()
    current_date_str = now.strftime('%Y-%m-%d')

    datos_cliente_limpios = _limpiar_datos_para_json(datos_cliente)

    schema_cliente = f"""
ESQUEMA CLIENTE - MIS PEDIDOS Y COMPRAS
Fecha actual: {current_date_str}

DATOS DEL CLIENTE ACTUAL:
- ID: {datos_cliente_limpios.get('id', 'current_user')}
- Nombre: {datos_cliente_limpios.get('nombre_cliente', 'Cliente')}

ESTRUCTURA EXACTA DE TU BASE DE DATOS:

MODELOS DISPONIBLES:
1. PedidoModel (PEDIDOS) - Campos: id, usuario, carrito, forma_pago, fecha, total, estado
2. DetallePedidoModel (DETALLES) - Campos: id, pedido, producto, cantidad, precio_unitario, subtotal

FILTROS CORRECTOS Y OBLIGATORIOS:
- Para PedidoModel: SIEMPRE usar "usuario__id": {datos_cliente_limpios.get('id', 'current_user')}
- Para DetallePedidoModel: SIEMPRE usar "pedido__usuario__id": {datos_cliente_limpios.get('id', 'current_user')}

ESTADOS VÁLIDOS para PedidoModel:
- 'pendiente', 'pagando', 'pagado', 'cancelado'

CAMPOS PARA FILTRAR PEDIDOS:
- fecha, total, estado, forma_pago__nombre
- Ejemplos: "fecha__gte", "total__gte", "estado__exact", "forma_pago__nombre__icontains"

CAMPOS PARA FILTRAR DETALLES/PRODUCTOS:
- producto__nombre, cantidad, precio_unitario, subtotal
- producto__marca__nombre, producto__subcategoria__nombre

NUNCA USAR ESTOS FILTROS (NO EXISTEN):
- usuario__nombre, cliente__nombre, cliente__id, direccion_entrega, usuario__username
No hay dirección de entrega en estos modelos.
"""

    system_instruction = f"""
Eres un asistente virtual especializado en reportes de compras para CLIENTES. 
Fecha actual: {current_date_str}

ANALIZA la consulta del cliente y DEVUELVE JSON con esta estructura:

{{
  "tipo_reporte": "pedidos" o "ventas",
  "formato": "pantalla", 
  "filtros": {{ "campo": "valor" }},
  "agrupacion": ["campo"],
  "calculos": {{ "nombre": "Funcion('campo')" }},
  "orden": ["campo"],
  "limite": número,
  "error": null
}}

REGLAS ABSOLUTAS:

1. SEGURIDAD: Todos los filtros deben incluir el ID del usuario actual
   - Para "pedidos": "usuario__id": {datos_cliente_limpios.get('id', 'current_user')}
   - Para "ventas": "pedido__usuario__id": {datos_cliente_limpios.get('id', 'current_user')}

2. SOLO USAR CAMPOS QUE EXISTEN:
   - PedidoModel: usuario, fecha, total, estado, forma_pago
   - DetallePedidoModel: pedido, producto, cantidad, precio_unitario, subtotal

3. EJEMPLOS CORRECTOS:

Usuario: "mis pedidos más caros"
Respuesta: {{
  "tipo_reporte": "pedidos",
  "filtros": {{ "usuario__id": {datos_cliente_limpios.get('id', 'current_user')} }},
  "orden": ["-total"],
  "limite": 5
}}

Usuario: "productos que más compro"
Respuesta: {{
  "tipo_reporte": "ventas",
  "filtros": {{ "pedido__usuario__id": {datos_cliente_limpios.get('id', 'current_user')} }},
  "agrupacion": ["producto__nombre", "producto__marca__nombre"],
  "calculos": {{
    "veces_comprado": "Count('id')",
    "total_unidades": "Sum('cantidad')",
    "total_gastado": "Sum('subtotal')"
  }},
  "orden": ["-veces_comprado"]
}}

Usuario: "pedidos pendientes"
Respuesta: {{
  "tipo_reporte": "pedidos",
  "filtros": {{ 
    "usuario__id": {datos_cliente_limpios.get('id', 'current_user')},
    "estado": "pendiente"
  }},
  "orden": ["-fecha"]
}}

Usuario: "mis últimos pedidos"
Respuesta: {{
  "tipo_reporte": "pedidos",
  "filtros": {{ "usuario__id": {datos_cliente_limpios.get('id', 'current_user')} }},
  "orden": ["-fecha"],
  "limite": 10
}}

IMPORTANTE: Si el usuario pregunta sobre productos, compras frecuentes, o qué compra más, usar "ventas".
Si pregunta sobre pedidos, órdenes, compras, usar "pedidos".
"""

    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1
        )

        response = model.generate_content(
            [system_instruction, schema_cliente, user_prompt],
            generation_config=generation_config
        )

        raw_response_text = (response.text or "").strip()
        print(f"[Gemini Cliente] Raw JSON response:\n{raw_response_text}")

        cleaned = raw_response_text.removeprefix("```json").removesuffix("```").strip()
        if not (cleaned.startswith('{') and cleaned.endswith('}')):
            i, j = cleaned.find('{'), cleaned.rfind('}')
            if i != -1 and j != -1 and j > i:
                cleaned = cleaned[i:j+1]
            else:
                raise json.JSONDecodeError("No JSON object found", cleaned, 0)

        parsed = json.loads(cleaned)
        interp = _normalize_interpretacion(parsed, default_tipo="pedidos")

        # CORRECCIÓN AUTOMÁTICA DE FILTROS
        interp = _corregir_filtros_automaticamente(interp, datos_cliente)

        return interp

    except Exception as e:
        print(f"[ERROR] Gemini cliente failed -> falling back to naive. Reason: {e}")
        return _naive_interpret_cliente(user_prompt, datos_cliente)

def _corregir_filtros_automaticamente(interpretacion, datos_cliente):
    """Corrige automáticamente los filtros para que sean compatibles con tus modelos"""
    filtros = interpretacion.get("filtros", {})
    tipo_reporte = interpretacion.get("tipo_reporte")
    
    # LISTA DE FILTROS PROHIBIDOS (que no existen en tus modelos)
    filtros_prohibidos = [
        'usuario__nombre', 'usuario__nombre__exact', 'usuario__nombre__icontains',
        'cliente', 'cliente__id', 'cliente__nombre', 'cliente__nombre__exact',
        'direccion_entrega', 'usuario__username', 'usuario__first_name'
    ]
    
    # Remover filtros prohibidos
    for filtro_prohibido in filtros_prohibidos:
        if filtro_prohibido in filtros:
            print(f"[CORRECCIÓN] Removiendo filtro prohibido: {filtro_prohibido}")
            del filtros[filtro_prohibido]
    
    # AGREGAR FILTROS DE SEGURIDAD OBLIGATORIOS
    if tipo_reporte == "pedidos":
        filtros["usuario__id"] = datos_cliente.get('id')
        print(f"[CORRECCIÓN] Agregado filtro seguridad: usuario__id = {datos_cliente.get('id')}")
    elif tipo_reporte == "ventas":
        filtros["pedido__usuario__id"] = datos_cliente.get('id')
        print(f"[CORRECCIÓN] Agregado filtro seguridad: pedido__usuario__id = {datos_cliente.get('id')}")
    
    # Corregir nombres de campos si es necesario
    correcciones_campos = {
        'cliente_id': 'usuario__id',
        'user_id': 'usuario__id',
        'customer_id': 'usuario__id',
    }
    
    for campo_incorrecto, campo_correcto in correcciones_campos.items():
        if campo_incorrecto in filtros:
            filtros[campo_correcto] = filtros.pop(campo_incorrecto)
            print(f"[CORRECCIÓN] Campo corregido: {campo_incorrecto} -> {campo_correcto}")
    
    interpretacion["filtros"] = filtros
    return interpretacion

def _naive_interpret_cliente(user_prompt: str, datos_cliente: dict):
    """Interpretación básica para clientes sin Gemini - CORREGIDO"""
    p = (user_prompt or "").lower()
    
    # Filtros de seguridad base CORREGIDOS
    filtros_base_pedidos = {"usuario__id": datos_cliente.get('id')}
    filtros_base_ventas = {"pedido__usuario__id": datos_cliente.get('id')}
    
    tipo = "pedidos"
    if any(palabra in p for palabra in ['producto', 'comprado', 'compro', 'frecuente', 'artículo']):
        tipo = "ventas"
        agrupacion = ["producto__nombre", "producto__marca__nombre"]
        calculos = {
            "veces_comprado": "Count('id')",
            "total_unidades": "Sum('cantidad')",
            "total_gastado": "Sum('subtotal')"
        }
        orden = ["-veces_comprado"]
        limite = 10
    else:
        agrupacion = []
        calculos = {}
        orden = ["-fecha"]
        limite = 20
    
    # Filtros específicos
    filtros = filtros_base_pedidos if tipo == "pedidos" else filtros_base_ventas
    
    if "caro" in p or "costoso" in p or "mayor precio" in p:
        orden = ["-total"]
        limite = 5
    
    if "barato" in p or "económico" in p or "menor precio" in p:
        orden = ["total"]
        limite = 5
    
    if "último" in p or "reciente" in p:
        orden = ["-fecha"]
        limite = 10
    
    if "pendiente" in p or "procesando" in p:
        if tipo == "pedidos":
            filtros["estado"] = "pendiente"
    
    if "pagado" in p or "completado" in p:
        if tipo == "pedidos":
            filtros["estado"] = "pagado"
    
    if "cancelado" in p:
        if tipo == "pedidos":
            filtros["estado"] = "cancelado"
    
    if "año" in p or "año" in p:
        current_year = timezone.now().year
        if tipo == "pedidos":
            filtros["fecha__year"] = current_year
        elif tipo == "ventas":
            filtros["pedido__fecha__year"] = current_year
    
    if "mes" in p:
        current_month = timezone.now().month
        if tipo == "pedidos":
            filtros["fecha__month"] = current_month
        elif tipo == "ventas":
            filtros["pedido__fecha__month"] = current_month
    
    return _normalize_interpretacion({
        "tipo_reporte": tipo,
        "formato": "pantalla",
        "filtros": filtros,
        "agrupacion": agrupacion,
        "calculos": calculos,
        "orden": orden,
        "limite": limite,
        "error": None
    })



def _generar_respuesta_amigable(pregunta, datos, datos_cliente, tipo_reporte):
    """Genera una respuesta amigable basada en los datos"""
    if not datos:
        return f"📭 No encontré información específica para tu consulta sobre '{pregunta}'. ¿Podrías intentar con otra pregunta?"

    if tipo_reporte == "pedidos":
        if len(datos) == 1:
            pedido = datos[0]
            total = pedido.get('total', 0)
            if isinstance(total, str):
                try:
                    total = float(total)
                except (ValueError, TypeError):
                    total = 0

            return f"📦 Encontré tu pedido del {pedido.get('fecha', 'N/A')} por Bs. {total:.2f}. Estado: {pedido.get('estado', 'N/A')}"
        else:
            pedido = datos[0]
            total = pedido.get('total', 0)
            if isinstance(total, str):
                try:
                    total = float(total)
                except (ValueError, TypeError):
                    total = 0

            return f"📋 Encontré {len(datos)} pedidos relacionados con tu búsqueda. El más caro es del {pedido.get('fecha', 'N/A')} por Bs. {total:.2f}"

    elif tipo_reporte == "ventas":
        producto_top = datos[0] if datos else {}

        veces_comprado = producto_top.get('veces_comprado', 0)
        if isinstance(veces_comprado, str):
            try:
                veces_comprado = int(veces_comprado)
            except (ValueError, TypeError):
                veces_comprado = 0

        total_unidades = producto_top.get('total_unidades', 0)
        if isinstance(total_unidades, str):
            try:
                total_unidades = int(total_unidades)
            except (ValueError, TypeError):
                total_unidades = 0

        total_gastado = producto_top.get('total_gastado', 0)
        if isinstance(total_gastado, str):
            try:
                total_gastado = float(total_gastado)
            except (ValueError, TypeError):
                total_gastado = 0

        return f"🏆 Tu producto más comprado es '{producto_top.get('producto_nombre', 'N/A')}' - lo has comprado {veces_comprado} veces ({total_unidades} unidades) por Bs. {total_gastado:.2f}"

    else:
        return f"✅ Encontré {len(datos)} resultados relacionados con tu consulta sobre '{pregunta}'"

# ===================================================================
# ENDPOINTS CON @api_view
# ===================================================================

@api_view(['POST'])
def consulta_ia_cliente(request):
    """Consulta con IA sobre los pedidos del cliente"""
    cliente = request.user
    pregunta = request.data.get('pregunta', '').strip()
    
    if not pregunta:
        return Response(
            {"error": "Se requiere una pregunta"}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        datos_cliente = _obtener_datos_cliente(cliente)
        if not datos_cliente:
            return Response(
                {"error": "No se pudieron obtener los datos del cliente"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        datos_cliente['id'] = cliente.id

        interpretacion = _call_gemini_cliente(pregunta, datos_cliente)
        interpretacion["prompt"] = pregunta
        interpretacion["error"] = None

        print(f"🎯 Consulta cliente: {pregunta}")
        print(f"🔐 Interpretación con seguridad: {interpretacion}")

        queryset, hubo_agrupacion = _build_queryset(interpretacion)

        tipo_reporte = interpretacion.get("tipo_reporte")
        data_para_reporte = _serializar_datos(queryset, tipo_reporte, hubo_agrupacion)

        data_convertida = _convertir_tipos_numericos(data_para_reporte)
        data_limpia = _limpiar_datos_para_json(data_convertida)
        datos_cliente_limpios = _limpiar_datos_para_json(datos_cliente)

        respuesta_amigable = _generar_respuesta_amigable(
            pregunta, data_limpia, datos_cliente_limpios, tipo_reporte
        )

        return Response({
            "respuesta": respuesta_amigable,
            "datos": data_limpia,
            "tipo_consulta": interpretacion["tipo_reporte"],
            "total_resultados": len(data_limpia),
            "datos_cliente": {
                "total_pedidos": datos_cliente_limpios.get("total_pedidos", 0),
                "total_gastado": datos_cliente_limpios.get("total_gastado", 0),
                "producto_mas_comprado": datos_cliente_limpios.get("productos_frecuentes", [{}])[0] if datos_cliente_limpios.get("productos_frecuentes") else {}
            }
        })

    except Exception as e:
        print(f"[ERROR] Consulta IA cliente: {e}")
        traceback.print_exc()
        return Response({
            "respuesta": "Lo siento, hubo un error al procesar tu consulta. Por favor intenta con preguntas más específicas sobre tus pedidos.",
            "sugerencias": [
                "¿Cuál fue mi último pedido?",
                "¿Cuánto he gastado en total?",
                "¿Cuáles son mis productos más comprados?",
                "¿Tengo pedidos pendientes?"
            ]
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def estadisticas_cliente(request):
    """Obtiene estadísticas generales del cliente"""
    cliente = request.user
    
    try:
        datos_cliente = _obtener_datos_cliente(cliente)
        
        return Response({
            "estadisticas": {
                "total_pedidos": datos_cliente.get("total_pedidos", 0),
                "total_gastado": datos_cliente.get("total_gastado", 0),
                "promedio_por_pedido": datos_cliente.get("promedio_por_pedido", 0),
                "miembro_desde": datos_cliente.get("miembro_desde", "N/A"),
                "meses_como_cliente": datos_cliente.get("meses_como_cliente", 0)
            },
            "productos_frecuentes": datos_cliente.get("productos_frecuentes", []),
            "ultimo_pedido": datos_cliente.get("ultimo_pedido", {}),
            "pedidos_por_estado": datos_cliente.get("pedidos_por_estado", []),
            "fecha_consulta": timezone.now().strftime('%Y-%m-%d %H:%M')
        })
        
    except Exception as e:
        print(f"[ERROR] Estadísticas cliente: {e}")
        return Response(
            {"error": "Error al obtener estadísticas"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
def procesar_voz_cliente(request):
    """Procesa audio de voz del cliente"""
    cliente = request.user
    
    try:
        # Simular transcripción (en producción integrar con servicio real)
        consultas_comunes = [
            "mis últimos pedidos",
            "qué productos compro más seguido", 
            "cuánto he gastado este mes",
            "mis pedidos pendientes",
            "cuál fue mi último pedido"
        ]
        texto_simulado = random.choice(consultas_comunes)
        
        datos_cliente = _obtener_datos_cliente(cliente)
        datos_cliente['id'] = cliente.id
        
        interpretacion = _call_gemini_cliente(texto_simulado, datos_cliente)
        queryset, hubo_agrupacion = _build_queryset(interpretacion)
        
        tipo_reporte = interpretacion.get("tipo_reporte")
        datos = _serializar_datos(queryset, tipo_reporte, hubo_agrupacion)
        
        respuesta = _generar_respuesta_amigable(texto_simulado, datos, datos_cliente, tipo_reporte)
        
        return Response({
            "texto_transcrito": texto_simulado,
            "respuesta": respuesta,
            "datos": datos,
            "accion_sugerida": _sugerir_accion(texto_simulado)
        })
        
    except Exception as e:
        print(f"[ERROR] Procesar voz cliente: {e}")
        return Response(
            {"error": "Error al procesar el audio. Intenta nuevamente."}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

def _sugerir_accion(texto):
    """Sugiere una acción basada en la consulta"""
    texto = texto.lower()
    
    if any(palabra in texto for palabra in ['pedido', 'orden']):
        return "ver_historial_pedidos"
    elif any(palabra in texto for palabra in ['producto', 'artículo']):
        return "ver_productos_frecuentes"
    elif any(palabra in texto for palabra in ['gasto', 'dinero']):
        return "ver_estadisticas"
    else:
        return "explorar_reportes"

@api_view(['GET'])
def opciones_filtros_cliente(request):
    """Obtiene opciones para los filtros"""
    cliente = request.user
    
    try:
        estados = PedidoModel.objects.filter(
            usuario=cliente
        ).values_list('estado', flat=True).distinct()
        
        tipos_pago = PedidoModel.objects.filter(
            usuario=cliente
        ).values_list('forma_pago__nombre', flat=True).distinct()
        
        fechas = PedidoModel.objects.filter(
            usuario=cliente
        ).aggregate(
            min_fecha=Min('fecha'),
            max_fecha=Max('fecha')
        )
        
        return Response({
            'estados': list(estados),
            'tipos_pago': list(tipos_pago),
            'rango_fechas': {
                'min': fechas['min_fecha'],
                'max': fechas['max_fecha']
            }
        })
        
    except Exception as e:
        return Response(
            {"error": "Error al obtener opciones de filtros"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
def generar_reporte_cliente(request):
    """Genera reporte con filtros personalizados - CORREGIDO"""
    cliente = request.user
    filtros = request.data.get('filtros', {})
    
    print(f"[Reporte Cliente] Filtros recibidos: {filtros}")
    print(f"[Reporte Cliente] Usuario: {cliente.id} - {cliente.username}")
    
    try:
        # Siempre filtrar por el usuario actual
        queryset = PedidoModel.objects.filter(usuario=cliente)
        
        # Aplicar filtros de manera segura
        if filtros.get('fecha_desde'):
            queryset = queryset.filter(fecha__gte=filtros['fecha_desde'])
        if filtros.get('fecha_hasta'):
            queryset = queryset.filter(fecha__lte=filtros['fecha_hasta'])
        if filtros.get('estado'):
            queryset = queryset.filter(estado=filtros['estado'])
        if filtros.get('tipo_pago'):
            queryset = queryset.filter(forma_pago__nombre=filtros['tipo_pago'])
        if filtros.get('monto_minimo'):
            queryset = queryset.filter(total__gte=float(filtros['monto_minimo']))
        if filtros.get('monto_maximo'):
            queryset = queryset.filter(total__lte=float(filtros['monto_maximo']))
        
        # Ordenar por fecha descendente por defecto
        pedidos = queryset.order_by('-fecha')
        
        # Serializar datos
        serializer = PedidoClienteSerializer(pedidos, many=True)
        
        print(f"[Reporte Cliente] Éxito: {pedidos.count()} pedidos encontrados")
        
        return Response({
            "respuesta": f"Reporte generado con {pedidos.count()} pedidos",
            "datos": serializer.data,
            "tipo_consulta": "pedidos_filtrados",
            "total_resultados": pedidos.count(),
            "filtros_aplicados": filtros
        })
        
    except Exception as e:
        print(f"[ERROR] Generar reporte: {e}")
        import traceback
        traceback.print_exc()
        
        return Response(
            {"error": f"Error al generar reporte: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
# En tu views.py - Agrega este endpoint

@api_view(['POST'])
def generar_pdf_reporte(request):
    """Genera un PDF del reporte basado en los filtros aplicados - CORREGIDO"""
    cliente = request.user
    filtros = request.data.get('filtros', {})
    
    print(f"[PDF Reporte] Filtros recibidos: {filtros}")
    print(f"[PDF Reporte] Usuario: {cliente.id} - {cliente.username}")
    
    try:
        # Siempre filtrar por el usuario actual
        queryset = PedidoModel.objects.filter(usuario=cliente)
        
        # Aplicar filtros de manera segura
        if filtros.get('fecha_desde'):
            queryset = queryset.filter(fecha__gte=filtros['fecha_desde'])
        if filtros.get('fecha_hasta'):
            queryset = queryset.filter(fecha__lte=filtros['fecha_hasta'])
        if filtros.get('estado'):
            queryset = queryset.filter(estado=filtros['estado'])
        if filtros.get('tipo_pago'):
            queryset = queryset.filter(forma_pago__nombre=filtros['tipo_pago'])
        if filtros.get('monto_minimo'):
            queryset = queryset.filter(total__gte=float(filtros['monto_minimo']))
        if filtros.get('monto_maximo'):
            queryset = queryset.filter(total__lte=float(filtros['monto_maximo']))
        
        # Ordenar por fecha descendente por defecto
        pedidos = queryset.order_by('-fecha')
        
        # Serializar datos
        serializer = PedidoClienteSerializer(pedidos, many=True)
        data = serializer.data
        
        # Crear interpretación para el generador de PDF
        interpretacion = {
            'prompt': f"Reporte de Pedidos - {cliente.get_full_name() or cliente.username}",
            'tipo_reporte': 'pedidos_filtrados',
            'filtros_aplicados': filtros,
            'total_resultados': pedidos.count(),
            'fecha_consulta': timezone.now().strftime('%Y-%m-%d %H:%M')
        }
        
        # CORRECCIÓN: Usar la función correcta
        from .generators import generar_reporte_cliente_pdf
        response = generar_reporte_cliente_pdf(data, interpretacion)
        
        print(f"[PDF Reporte] Éxito: PDF generado con {pedidos.count()} pedidos")
        
        return response
        
    except Exception as e:
        print(f"[ERROR] Generar PDF reporte: {e}")
        import traceback
        traceback.print_exc()
        
        return Response(
            {"error": f"Error al generar PDF: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
# En tu views.py - Asegúrate de que esté usando la función correcta

@api_view(['POST'])
def generar_pdf_consulta_ia(request):
    """Genera un PDF de la consulta IA realizada - CORREGIDO"""
    cliente = request.user
    pregunta = request.data.get('pregunta', '').strip()
    
    if not pregunta:
        return Response(
            {"error": "Se requiere una pregunta para generar el PDF"}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Obtener datos del cliente
        datos_cliente = _obtener_datos_cliente(cliente)
        if not datos_cliente:
            return Response(
                {"error": "No se pudieron obtener los datos del cliente"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        datos_cliente['id'] = cliente.id

        # Interpretar consulta con IA
        interpretacion = _call_gemini_cliente(pregunta, datos_cliente)
        interpretacion["prompt"] = pregunta

        print(f"🎯 Generando PDF para consulta: {pregunta}")

        # Construir queryset
        queryset, hubo_agrupacion = _build_queryset(interpretacion)

        # Serializar datos
        tipo_reporte = interpretacion.get("tipo_reporte")
        data_para_reporte = _serializar_datos(queryset, tipo_reporte, hubo_agrupacion)

        # Limpiar datos para el PDF
        data_limpia = _limpiar_datos_para_json(data_para_reporte)

        # Preparar interpretación para el PDF
        interpretacion_pdf = {
            'prompt': f"Consulta: {pregunta}",
            'tipo_reporte': tipo_reporte,
            'total_resultados': len(data_limpia),
            'fecha_consulta': timezone.now().strftime('%Y-%m-%d %H:%M')
        }

        # CORRECCIÓN: Usar la función correcta
        from .generators import generar_reporte_cliente_pdf
        response = generar_reporte_cliente_pdf(data_limpia, interpretacion_pdf)
        
        print(f"📄 PDF generado exitosamente para: {pregunta}")
        
        return response

    except Exception as e:
        print(f"[ERROR] Generar PDF consulta IA: {e}")
        import traceback
        traceback.print_exc()
        return Response(
            {"error": f"Error al generar el PDF: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )