# management/commands/generar_historico_precios.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import random
from decimal import Decimal
from producto.models import ProductoModel, CambioPrecioModel
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Genera datos históricos de cambios de precios para los últimos 2 años'

    def add_arguments(self, parser):
        parser.add_argument(
            '--productos',
            type=int,
            default=120,
            help='Número de productos para generar historial'
        )
        parser.add_argument(
            '--max-cambios',
            type=int,
            default=20,
            help='Máximo número de cambios por producto'
        )

    def handle(self, *args, **options):
        productos_count = options['productos']
        max_cambios = options['max_cambios']
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Iniciando generación de historial para {productos_count} productos...'
            )
        )
        
        productos = ProductoModel.objects.all()[:productos_count]
        
        if not productos:
            self.stdout.write(
                self.style.ERROR('No se encontraron productos')
            )
            return
        
        total_cambios_creados = 0
        
        for producto in productos:
            cambios_creados = self.generar_historial_producto(producto, max_cambios)
            total_cambios_creados += cambios_creados
            
            self.stdout.write(
                f'Producto {producto.id}: {cambios_creados} cambios creados'
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Completado. Total cambios: {total_cambios_creados}'
            )
        )
    
    def generar_historial_producto(self, producto, max_cambios):
        """Genera historial de cambios de precio para un producto"""
        
        precio_actual_contado = producto.precio_contado
        precio_actual_cuota = producto.precio_cuota
        
        fecha_fin = timezone.now().date()
        fecha_inicio = fecha_fin - timedelta(days=365)  # 1 años
        
        num_cambios = random.randint(12, max_cambios)  # Más cambios para más variabilidad
        fechas = self.generar_fechas_aleatorias(fecha_inicio, fecha_fin, num_cambios)
        
        cambios_creados = 0
        precio_anterior_contado = precio_actual_contado
        precio_anterior_cuota = precio_actual_cuota
        
        fechas.sort()
        
        for i, fecha in enumerate(fechas):
            try:
                if i == len(fechas) - 1:
                    # Último cambio lleva a precios actuales
                    precio_nuevo_contado = precio_actual_contado
                    precio_nuevo_cuota = precio_actual_cuota
                else:
                    # Decidir qué tipo de cambio hacer
                    tipo_cambio = self.elegir_tipo_cambio()
                    
                    if tipo_cambio == 'solo_contado':
                        precio_nuevo_contado, _ = self.generar_nuevo_precio(precio_anterior_contado)
                        precio_nuevo_cuota = precio_anterior_cuota  # Mantener igual
                    
                    elif tipo_cambio == 'solo_cuota':
                        precio_nuevo_cuota, _ = self.generar_nuevo_precio(precio_anterior_cuota)
                        precio_nuevo_contado = precio_anterior_contado  # Mantener igual
                    
                    elif tipo_cambio == 'ambos_misma_variacion':
                        variacion = self.generar_variacion()
                        precio_nuevo_contado = precio_anterior_contado * Decimal(1 + variacion)
                        precio_nuevo_cuota = precio_anterior_cuota * Decimal(1 + variacion)
                    
                    else:  # 'ambos_diferente_variacion'
                        variacion_contado = self.generar_variacion()
                        variacion_cuota = self.generar_variacion()
                        precio_nuevo_contado = precio_anterior_contado * Decimal(1 + variacion_contado)
                        precio_nuevo_cuota = precio_anterior_cuota * Decimal(1 + variacion_cuota)
                
                # Asegurar precios válidos
                precio_nuevo_contado = max(round(precio_nuevo_contado, 2), Decimal('10.00'))
                precio_nuevo_cuota = max(round(precio_nuevo_cuota, 2), Decimal('10.00'))
                
                # Crear el cambio de precio
                CambioPrecioModel.objects.create(
                    producto=producto,
                    precio_anterior=precio_anterior_contado,
                    precio_nuevo=precio_nuevo_contado,
                    precio_cuota_anterior=precio_anterior_cuota,
                    precio_cuota_nuevo=precio_nuevo_cuota,
                    fecha_cambio=fecha
                )
                
                cambios_creados += 1
                precio_anterior_contado = precio_nuevo_contado
                precio_anterior_cuota = precio_nuevo_cuota
                
            except Exception as e:
                logger.error(f"Error producto {producto.id}: {e}")
                continue
        
        return cambios_creados
    
    def elegir_tipo_cambio(self):
        """Elige qué tipo de cambio de precio realizar"""
        tipos_cambio = [
            ('solo_contado', 30),      # 30% solo cambia contado
            ('solo_cuota', 25),        # 25% solo cambia cuota
            ('ambos_misma_variacion', 20),  # 20% ambos con misma variación
            ('ambos_diferente_variacion', 25),  # 25% ambos con variaciones diferentes
        ]
        
        opciones, pesos = zip(*tipos_cambio)
        return random.choices(opciones, weights=pesos, k=1)[0]
    
    def generar_variacion(self):
        """Genera una variación de precio realista"""
        tipos_variacion = [
            ('subida_pequena', 0.02, 0.05, 20),    # 2-5% de aumento
            ('subida_media', 0.05, 0.10, 10),      # 5-10% de aumento  
            ('subida_grande', 0.10, 0.20, 5),      # 10-20% de aumento
            ('bajada_pequena', -0.05, -0.02, 25),  # 2-5% de descuento
            ('bajada_media', -0.10, -0.05, 15),    # 5-10% de descuento
            ('bajada_grande', -0.20, -0.10, 5),    # 10-20% de descuento
            ('estable', -0.01, 0.01, 20),          # Precio estable
        ]
        
        # Elegir tipo de variación basado en pesos
        opciones = []
        pesos = []
        for tipo in tipos_variacion:
            opciones.append(tipo)
            pesos.append(tipo[3])
        
        tipo_elegido = random.choices(opciones, weights=pesos, k=1)[0]
        _, min_porcentaje, max_porcentaje, _ = tipo_elegido
        
        return random.uniform(min_porcentaje, max_porcentaje)
    
    def generar_nuevo_precio(self, precio_actual):
        """Genera un nuevo precio con variación"""
        variacion = self.generar_variacion()
        precio_nuevo = precio_actual * Decimal(1 + variacion)
        precio_nuevo = max(round(precio_nuevo, 2), Decimal('10.00'))
        return precio_nuevo, variacion
    
    def generar_fechas_aleatorias(self, fecha_inicio, fecha_fin, num_fechas):
        """Genera fechas aleatorias distribuidas"""
        fechas = []
        rango_dias = (fecha_fin - fecha_inicio).days
        
        for _ in range(num_fechas):
            # Distribuir las fechas de manera más realista (más cambios recientes)
            dias_aleatorios = int(random.betavariate(2, 5) * rango_dias)
            fecha = fecha_inicio + timedelta(days=dias_aleatorios)
            
            # Asegurar que no haya fechas duplicadas
            while fecha in fechas:
                dias_aleatorios = int(random.betavariate(2, 5) * rango_dias)
                fecha = fecha_inicio + timedelta(days=dias_aleatorios)
            
            fechas.append(fecha)
        
        return fechas