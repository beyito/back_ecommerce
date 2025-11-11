import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score
import joblib
from math import sqrt

def entrenar_modelo():
    # Cargar datos
    datos = pd.read_csv('ia/ml/ventas_sinteticas.csv')  # Asegúrate del nombre del archivo

    # Mostrar resumen
    print("Datos cargados correctamente:")
    print(datos.head())

    # Variables independientes (features)
    X = datos[['num_pedidos', 'promedio_pedido', 'porcentaje_credito',
               'porcentaje_contado', 'mes', 'dia_semana', 'temporada']]

    # Variable dependiente (lo que queremos predecir)
    y = datos['total_ventas']

    # Separar en entrenamiento y prueba
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # Crear y entrenar el modelo
    modelo = RandomForestRegressor(
        n_estimators=200,
        random_state=42,
        n_jobs=-1
    )

    modelo.fit(X_train, y_train)

    # Evaluar
    y_pred = modelo.predict(X_test)
    rmse = sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    print(f"✅ Entrenamiento completado:")
    print(f"RMSE: {rmse:.2f}")
    print(f"R²: {r2:.2f}")

    # Guardar modelo entrenado
    joblib.dump(modelo, 'ia/ml/modelo_random_forest_ventas.pkl')
    print("📦 Modelo guardado en ia/ml/modelo_random_forest_ventas.pkl")

if __name__ == "__main__":
    entrenar_modelo()
