import sys
import os

# Asegurar que el directorio raíz está en el path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_layer.unified_dataset import build_unified_daily


def main():
    print("Reconstruyendo base de datos unificada diaria (OnChainUnifiedDaily)...")
    rows = build_unified_daily()
    print(f"Listo: {rows} filas (date, símbolo, métrica) escritas.")


if __name__ == "__main__":
    main()
