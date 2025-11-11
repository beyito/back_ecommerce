# ventas_sinteticas.py
import pandas as pd
import numpy as np
from datetime import date, timedelta
import random

# 🔧 Parámetros
hoy = date.today()
inicio = hoy - timedelta(days=3*365)  # 3 años atrás
fechas = pd.date_range(inicio, hoy, freq='D')

# 🔹 Simular ventas por día
data = []
for fecha in fechas:
    mes = fecha.month
    dia_semana = fecha.weekday()

    # Temporadas altas: diciembre y julio
    temporada_alta = 1 if mes in [12, 7] else 0

    # Base de número de pedidos por día (más en fines de semana)
    base_pedidos = 15 + (5 if dia_semana >= 5 else 0)
    if temporada_alta:
        base_pedidos += random.randint(5, 20)

    num_pedidos = int(np.random.normal(base_pedidos, 4))
    num_pedidos = max(num_pedidos, 1)

    # Distribución de formas de pago
    porcentaje_credito = np.random.uniform(0.2, 0.6)
    porcentaje_contado = 1 - porcentaje_credito

    # Promedios
    promedio_pedido = np.random.uniform(800, 5000)
    total_ventas = num_pedidos * promedio_pedido

    data.append([
        fecha,
        round(total_ventas, 2),
        num_pedidos,
        round(promedio_pedido, 2),
        round(porcentaje_credito, 2),
        round(porcentaje_contado, 2),
        mes,
        dia_semana,
        temporada_alta
    ])

# Crear DataFrame
df_ventas = pd.DataFrame(data, columns=[
    "fecha", "total_ventas", "num_pedidos", "promedio_pedido",
    "porcentaje_credito", "porcentaje_contado",
    "mes", "dia_semana", "temporada"
])

# Guardar en CSV
df_ventas.to_csv("ventas_sinteticas.csv", index=False, encoding="utf-8")
print("✅ Dataset sintético generado: ventas_sinteticas.csv")
print(df_ventas.head(10))
