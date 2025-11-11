import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
import joblib
from math import sqrt
from sklearn.metrics import r2_score
# Leer CSV
df = pd.read_csv("ia/ml/ventas_sinteticas.csv")

# Características
X = df[["promedio_pedido","porcentaje_credito","porcentaje_contado","mes","dia_semana","temporada"]]

# Variable a predecir (cambiar según lo que quieras)
y = df["num_pedidos"]  # o df["total_ventas"]
    
# Dividir datos
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# Entrenar modelo
modelo = RandomForestRegressor(n_estimators=100, random_state=42)
modelo.fit(X_train, y_train)

# Evaluar
y_pred = modelo.predict(X_test)
rmse = sqrt(mean_squared_error(y_test, y_pred))
r2 = r2_score(y_test, y_pred)
print("RMSE:", rmse)
print("R²:", r2)

# Guardar modelo
joblib.dump(modelo, "ia/ml/modelo_random_forest_pedidos.pkl")

