import streamlit as st
import yaml
import os
import glob

st.set_page_config(page_title="Configuración de Estrategias", layout="wide")
st.title("Configuración de Estrategias YAML")

strategy_files = glob.glob("config/strategies/*.yaml")
strategy_names = [os.path.basename(f) for f in strategy_files]

selected_strategy = st.selectbox("Seleccionar Estrategia para Editar", ["-- Nueva --"] + strategy_names)

if selected_strategy == "-- Nueva --":
    content = ""
    file_name = st.text_input("Nombre del archivo (ej. nueva_estrategia.yaml)")
else:
    file_name = selected_strategy
    with open(os.path.join("config", "strategies", selected_strategy), "r", encoding='utf-8') as f:
        content = f.read()

st.write("Edita el YAML directamente:")
new_content = st.text_area("Contenido YAML", content, height=400)

if st.button("Guardar"):
    if file_name:
        try:
            # Validar YAML
            yaml.safe_load(new_content)
            path = os.path.join("config", "strategies", file_name)
            with open(path, "w", encoding='utf-8') as f:
                f.write(new_content)
            st.success(f"Estrategia guardada en {path}")
        except Exception as e:
            st.error(f"Error en formato YAML: {e}")
    else:
        st.error("Por favor ingresa un nombre de archivo.")
