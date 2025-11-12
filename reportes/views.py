# reportes/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.http import HttpResponse
from django.conf import settings

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
from venta.models import CarritoModel, DetalleCarritoModel, PedidoModel, DetallePedidoModel, FormaPagoModel, PlanPagoModel, PagoModel, MetodoPagoModel

# from .permissions import IsAdminOrStaff
from .generators import generar_reporte_pdf, generar_reporte_excel

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
    """Interpretación básica sin Gemini"""
    p = (user_prompt or "").lower()
    tipo = "productos"
    if "pedido" in p or "venta" in p: tipo = "pedidos"
    elif "pago" in p or "cuota" in p: tipo = "pagos"
    elif "cliente" in p or "usuario" in p: tipo = "clientes"
    elif "carrito" in p: tipo = "carritos"
    elif "categoria" in p: tipo = "categorias"
    elif "marca" in p: tipo = "marcas"
    elif "inventario" in p or "stock" in p: tipo = "inventario"
    
    calculos = {}
    if "cantidad" in p or "conteo" in p or "total" in p:
        calculos = {"cantidad": "Count('id')"}
    if "suma" in p or "monto" in p:
        calculos = {"total": "Sum('total')"}
    
    filtros = {}
    if "activo" in p: filtros["is_active"] = True
    if "inactivo" in p: filtros["is_active"] = False
    if "stock bajo" in p: filtros["stock__lt"] = 5
    if "sin stock" in p: filtros["stock"] = 0
    if "pagado" in p: filtros["estado"] = "pagado"
    if "pendiente" in p: filtros["estado"] = "pendiente"
    
    return _normalize_interpretacion({
        "tipo_reporte": tipo, "formato": "pantalla", "filtros": filtros,
        "agrupacion": [], "calculos": calculos, "orden": [], "error": None,
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

    def _build_queryset(self, interpretacion):
        """Construye el queryset según la interpretación"""
        tipo = interpretacion.get("tipo_reporte")
        filtros_dict = interpretacion.get("filtros", {})
        agrupacion_list = interpretacion.get("agrupacion", [])
        calculos_dict = interpretacion.get("calculos", {})
        orden_list = interpretacion.get("orden", [])

        ModelClass = None
        base_queryset = None

        # Mapeo de tipos a modelos
        if tipo == "productos":
            ModelClass = ProductoModel
            base_queryset = ProductoModel.objects.select_related('marca', 'subcategoria', 'subcategoria__categoria')
        elif tipo == "categorias":
            ModelClass = CategoriaModel
            base_queryset = CategoriaModel.objects.all()
        elif tipo == "marcas":
            ModelClass = MarcaModel
            base_queryset = MarcaModel.objects.all()
        elif tipo == "carritos":
            ModelClass = CarritoModel
            base_queryset = CarritoModel.objects.select_related('usuario')
        elif tipo == "pedidos":
            ModelClass = PedidoModel
            base_queryset = PedidoModel.objects.select_related('usuario', 'forma_pago')
        elif tipo == "pagos":
            ModelClass = PagoModel
            base_queryset = PagoModel.objects.select_related('plan_pago', 'metodo_pago')
        elif tipo == "clientes":
            ModelClass = Usuario
            base_queryset = Usuario.objects.all()
        elif tipo == "ventas":
            ModelClass = DetallePedidoModel
            base_queryset = DetallePedidoModel.objects.select_related('pedido', 'producto')
        elif tipo == "inventario":
            ModelClass = ProductoModel
            base_queryset = ProductoModel.objects.select_related('marca', 'subcategoria')
        elif tipo == "planes_pago":
            ModelClass = PlanPagoModel
            base_queryset = PlanPagoModel.objects.select_related('pedido')
        else:
            raise ValueError(f"Tipo de reporte '{tipo}' no soportado.")

        # Aplicar filtros
        q_filtros = Q()
        for lookup, value in dict(filtros_dict).items():
            try:
                converted_value = self._validate_and_convert_value(ModelClass, lookup, value)
                q_filtros &= Q(**{lookup: converted_value})
            except (FieldDoesNotExist, ValueError, TypeError) as e:
                print(f"[WARN] Skipping invalid filter: {lookup}={repr(value)}. Reason: {e}")
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

        return queryset, hubo_agrupacion

# ===================================================================
# VISTA #1: GenerarReporteView (CON IA)
# ===================================================================
class GenerarReporteView(ReporteBaseView):

    def _call_gemini_api(self, user_prompt: str):
        if not GEMINI_CONFIGURED:
            return _naive_interpret(user_prompt)

        now = timezone.now()
        current_date_str = now.strftime('%Y-%m-%d')

        schema_definition = f"""
Esquema de Modelos para Ecommerce (Moneda: Bs.):
Fecha actual: {current_date_str}

1. Producto (ProductoModel):
   - Campos: id, nombre, descripcion, modelo, precio_contado, precio_cuota, stock
   - Campos: garantia_meses, fecha_registro, is_active
   - Relaciones: marca (-> MarcaModel), subcategoria (-> SubcategoriaModel), subcategoria__categoria (-> CategoriaModel)

2. Pedido (PedidoModel):
   - Campos: id, fecha, total, estado ('pendiente', 'pagando', 'pagado', 'cancelado')
   - Relaciones: usuario (-> Usuario), forma_pago (-> FormaPagoModel)

3. Pago (PagoModel):
   - Campos: id, fecha_pago, monto, comprobante, is_active
   - Relaciones: plan_pago (-> PlanPagoModel), metodo_pago (-> MetodoPagoModel)

4. Carrito (CarritoModel):
   - Campos: id, fecha, total, is_active
   - Relaciones: usuario (-> Usuario)

5. Cliente (Usuario):
   - Campos: id, username, first_name, last_name, email, ci, telefono, date_joined, is_active

6. DetallePedido (DetallePedidoModel):
   - Campos: id, cantidad, precio_unitario, subtotal
   - Relaciones: pedido (-> PedidoModel), producto (-> ProductoModel)

7. PlanPago (PlanPagoModel):
   - Campos: id, numero_cuota, monto, fecha_vencimiento, estado ('pendiente', 'pagado')

Tipos de reporte válidos: {list(VALID_TIPOS)}
"""
        system_instruction = f"""
Eres un asistente experto en bases de datos para un Ecommerce (Moneda: Bs.). Fecha actual: {current_date_str}.
Devuelve ÚNICAMENTE un JSON con esta estructura:

{{
  "tipo_reporte": "string", 
  "formato": "pantalla",
  "filtros": {{ "campo__lookup": "valor" }},
  "agrupacion": ["campo_para_agrupar"], 
  "calculos": {{ "nombre_del_calculo": "Funcion('campo')" }}, 
  "orden": ["campo_para_ordenar"], 
  "error": null | "string"
}}

Reglas:
- "tipo_reporte": uno de los tipos válidos.
- Usa campos EXACTOS del esquema.
- Para cálculos: "Sum('precio_contado')", "Count('id')", "Avg('stock')"
- Para fechas usa formato YYYY-MM-DD
- "error": solo si la solicitud es imposible
"""
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            generation_config = genai.types.GenerationConfig(response_mime_type="application/json")
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
            if hubo_agrupacion:
                data_para_reporte = list(queryset[:MAX_ROWS])
            else:
                # Campos por defecto para cada tipo
                campos_por_tipo = {
                    "productos": ['id', 'nombre', 'marca__nombre', 'precio_contado', 'stock', 'is_active'],
                    "pedidos": ['id', 'usuario__username', 'total', 'estado', 'fecha'],
                    "pagos": ['id', 'monto', 'fecha_pago', 'metodo_pago__nombre', 'plan_pago__numero_cuota'],
                    "carritos": ['id', 'usuario__username', 'total', 'fecha', 'is_active'],
                    "clientes": ['id', 'username', 'email', 'ci', 'telefono', 'date_joined'],
                    "categorias": ['id', 'nombre', 'descripcion', 'is_active'],
                    "marcas": ['id', 'nombre', 'is_active'],
                    "ventas": ['id', 'producto__nombre', 'cantidad', 'precio_unitario', 'subtotal'],
                    "inventario": ['id', 'nombre', 'marca__nombre', 'stock', 'precio_contado'],
                    "planes_pago": ['id', 'numero_cuota', 'monto', 'fecha_vencimiento', 'estado'],
                }
                
                campos = campos_por_tipo.get(tipo_reporte, [])
                if campos:
                    valid_fields = []
                    ModelClass = None
                    if tipo_reporte == "productos": ModelClass = ProductoModel
                    elif tipo_reporte == "pedidos": ModelClass = PedidoModel
                    # ... otros mapeos
                    
                    for f in campos:
                        try:
                            if ModelClass:
                                self._validate_and_convert_value(ModelClass, f, None)
                            valid_fields.append(f)
                        except (FieldDoesNotExist, ValueError):
                            print(f"[WARN] Campo por defecto no encontrado: {f}")
                    data_para_reporte = list(queryset.values(*valid_fields)[:MAX_ROWS])
                else:
                    data_para_reporte = list(queryset.values()[:MAX_ROWS])

            json_output = json.dumps(data_para_reporte, default=_json_converter)
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
            if hubo_agrupacion:
                data_para_reporte = list(queryset[:MAX_ROWS])
            else:
                data_para_reporte = list(queryset.values()[:MAX_ROWS])

            json_output = json.dumps(data_para_reporte, default=_json_converter)
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
        }
        date_field = DATE_FIELD_MAP.get(tipo, "id")

        if fecha_desde and fecha_hasta:
            filtros_out[f"{date_field}__range"] = [fecha_desde, fecha_hasta]
        elif fecha_desde:
            filtros_out[f"{date_field}__gte"] = fecha_desde
        elif fecha_hasta:
            filtros_out[f"{date_field}__lte"] = fecha_hasta

        # Stock (para productos)
        if tipo == "productos" and filtros_in.get("stockOp"):
            stock_valor = filtros_in.get("stockValor")
            if stock_valor is not None:
                filtros_out[f"stock__{filtros_in['stockOp']}"] = stock_valor

        # Precio (para productos)
        if tipo == "productos" and filtros_in.get("precioOp"):
            precio_valor = filtros_in.get("precioValor")
            if precio_valor is not None:
                filtros_out[f"precio_contado__{filtros_in['precioOp']}"] = precio_valor

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