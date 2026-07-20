import os
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix
from .feature_engineering import FeatureEngineer

class MLModelTrainer:
    def __init__(self, model_type="random_forest"):
        self.model_type = model_type
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.scaler = StandardScaler()
        self.feature_engineer = FeatureEngineer(target_lookahead=1, target_threshold=0.0)
        self.is_trained = False
        
    def train(self, df):
        """
        Entrena el modelo usando datos históricos y on-chain.
        Devuelve métricas del entrenamiento.
        """
        X, y = self.feature_engineer.prepare_data(df)
        
        # Split simple 80/20 (en series temporales es mejor no hacer shuffle)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
        
        # Escalar
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # Entrenar
        self.model.fit(X_train_scaled, y_train)
        self.is_trained = True
        
        # Evaluar
        y_pred = self.model.predict(X_test_scaled)
        
        metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'f1_score': f1_score(y_test, y_pred, average='weighted'),
            'confusion_matrix': confusion_matrix(y_test, y_pred).tolist()
        }
        
        return metrics
        
    def predict(self, df):
        """
        Devuelve predicciones que el strategy_engine convertirá en señales.
        """
        if not self.is_trained:
            raise ValueError("El modelo debe ser entrenado antes de predecir.")
            
        X, _ = self.feature_engineer.prepare_data(df)
        X_scaled = self.scaler.transform(X)
        return self.model.predict(X_scaled)
        
    def save_model(self, path):
        if not self.is_trained:
            raise ValueError("No se puede guardar un modelo sin entrenar.")
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'model_type': self.model_type
        }
        joblib.dump(model_data, path)
        return True
        
    def load_model(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"No se encontró el modelo en {path}")
            
        model_data = joblib.load(path)
        self.model = model_data['model']
        self.scaler = model_data['scaler']
        self.model_type = model_data['model_type']
        self.is_trained = True
        return True
