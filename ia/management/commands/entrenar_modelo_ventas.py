from django.core.management.base import BaseCommand
from venta.models import PedidoModel
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
import pandas as pd
import numpy as np
import joblib
from datetime import date, timedelta
from math import sqrt

class Command(BaseCommand):
    help = "Reentrena los modelos de predicción (ventas diarias, pedidos diarios y ventas mensuales)."

    def handle(self, *args, **kwargs):
        hoy = date.today()
        hace_dos_anios = hoy - timedelta(days=730)

        pedidos = PedidoModel.objects.filter(
            estado="pagado",
            fecha__range=(hace_dos_anios, hoy)
        )

        if not pedidos.exists():
            self.stdout.write(self.style.ERROR("❌ No hay pedidos disponibles para entrenar los modelos."))
            return

        # --- Convertir pedidos a DataFrame ---
        data = []
        for p in pedidos:
            data.append({
                "fecha": p.fecha,
                "total": float(p.total),
                "forma_pago": "credito" if "credito" in p.forma_pago.nombre.lower() else "contado"
            })

        df = pd.DataFrame(data)
        df["mes"] = pd.to_datetime(df["fecha"]).dt.month
        df["anio"] = pd.to_datetime(df["fecha"]).dt.year

        # --- 📅 Modelo de Ventas y Pedidos Diarios ---
        resumen_diario = df.groupby("fecha").agg(
            total_ventas=("total", "sum"),
            num_pedidos=("total", "count")
        ).reset_index()

        resumen_diario["promedio_pedido"] = resumen_diario["total_ventas"] / resumen_diario["num_pedidos"]

        # Cálculo de porcentaje crédito / contado diario
        creditos = df[df["forma_pago"] == "credito"].groupby("fecha").size()
        resumen_diario["porcentaje_credito"] = resumen_diario["fecha"].map(
            creditos / resumen_diario["num_pedidos"]
        ).fillna(0)
        resumen_diario["porcentaje_contado"] = 1 - resumen_diario["porcentaje_credito"]

        resumen_diario["mes"] = pd.to_datetime(resumen_diario["fecha"]).dt.month
        resumen_diario["dia_semana"] = pd.to_datetime(resumen_diario["fecha"]).dt.weekday
        resumen_diario["temporada"] = resumen_diario["mes"].isin([1, 6, 11, 12]).astype(int)

        # --- Modelo de Ventas Diarias ---
        X_ventas = resumen_diario[[
            "num_pedidos", "promedio_pedido", "porcentaje_credito",
            "porcentaje_contado", "mes", "dia_semana", "temporada"
        ]]
        y_ventas = resumen_diario["total_ventas"]

        modelo_ventas = RandomForestRegressor(n_estimators=100, random_state=42)
        modelo_ventas.fit(X_ventas, y_ventas)

        # --- Modelo de Pedidos Diarios ---
        X_pedidos = resumen_diario[[
            "promedio_pedido", "porcentaje_credito",
            "porcentaje_contado", "mes", "dia_semana", "temporada"
        ]]
        y_pedidos = resumen_diario["num_pedidos"]

        modelo_pedidos = RandomForestRegressor(n_estimators=100, random_state=42)
        modelo_pedidos.fit(X_pedidos, y_pedidos)

      # --- 📆 Modelo de Ventas Mensuales ---
        resumen_mensual = df.groupby(["anio", "mes"]).agg(
            total_ventas=("total", "sum"),
            num_pedidos=("total", "count")
        ).reset_index()

        # Calcular promedios y porcentajes por mes
        resumen_mensual["promedio_pedido"] = resumen_mensual["total_ventas"] / resumen_mensual["num_pedidos"]

       # Calcular número de pedidos de crédito por mes
        creditos_mensuales = df[df["forma_pago"] == "credito"].groupby(["anio", "mes"]).size().reset_index(name="creditos")

        # Unir con resumen_mensual
        resumen_mensual = resumen_mensual.merge(creditos_mensuales, on=["anio", "mes"], how="left")
        resumen_mensual["creditos"] = resumen_mensual["creditos"].fillna(0)

        # Porcentaje de crédito y contado
        resumen_mensual["porcentaje_credito"] = resumen_mensual["creditos"] / resumen_mensual["num_pedidos"]
        resumen_mensual["porcentaje_contado"] = 1 - resumen_mensual["porcentaje_credito"]
        resumen_mensual["temporada"] = resumen_mensual["mes"].isin([1, 6, 11, 12]).astype(int)
        # Variables predictoras (6)
        X_mensual = resumen_mensual[[
            "num_pedidos", "promedio_pedido", "porcentaje_credito",
            "porcentaje_contado", "mes", "temporada"
        ]]
        y_mensual = resumen_mensual["total_ventas"]

        modelo_mensual = RandomForestRegressor(n_estimators=100, random_state=42)
        modelo_mensual.fit(X_mensual, y_mensual)

        # --- Guardar modelos ---
        joblib.dump(modelo_ventas, "ia/ml/modelo_random_forest_ventas.pkl")
        joblib.dump(modelo_pedidos, "ia/ml/modelo_random_forest_pedidos.pkl")
        joblib.dump(modelo_mensual, "ia/ml/modelo_random_forest_ventas_mensuales.pkl")

        # --- Mostrar métricas ---
        pred_ventas = modelo_ventas.predict(X_ventas)
        rmse_ventas = sqrt(mean_squared_error(y_ventas, pred_ventas))
        r2_ventas = r2_score(y_ventas, pred_ventas)

        pred_pedidos = modelo_pedidos.predict(X_pedidos)
        rmse_pedidos = sqrt(mean_squared_error(y_pedidos, pred_pedidos))
        r2_pedidos = r2_score(y_pedidos, pred_pedidos)

        pred_mensual = modelo_mensual.predict(X_mensual)
        rmse_mensual = sqrt(mean_squared_error(y_mensual, pred_mensual))
        r2_mensual = r2_score(y_mensual, pred_mensual)

        self.stdout.write(self.style.SUCCESS("✅ Modelos reentrenados correctamente."))
        self.stdout.write(self.style.SUCCESS(
            f"📅 Ventas Diarias -> RMSE: {rmse_ventas:.2f}, R²: {r2_ventas:.2f}\n"
            f"🧾 Pedidos Diarios -> RMSE: {rmse_pedidos:.2f}, R²: {r2_pedidos:.2f}\n"
            f"📆 Ventas Mensuales -> RMSE: {rmse_mensual:.2f}, R²: {r2_mensual:.2f}"
        ))
