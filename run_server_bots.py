"""
Script de inicio de Trading Quant BOTS App (Binance).
Instancia independiente de la app principal, enfocada solo en los modulos
de operacion de bots: Live Monitor, Cartera & Riesgo, Operativa Spot/Fut,
P2P, Derivados & Futuros, Opciones/TWAP/POV, Copiloto IA y Conexion de APIs.
Corre en un puerto distinto (8001) para poder usarse en paralelo con la app
principal (run_server.py, puerto 8000).
"""
import subprocess
import sys
import os
import socket
import time
import webbrowser
import threading

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOST = "127.0.0.1"
PORT = 8001
URL = f"http://localhost:{PORT}"

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def banner():
    print(f"\n{CYAN}{BOLD}" + "=" * 50)
    print("   Trading Quant BOTS App - FastAPI + NiceGUI")
    print("=" * 50 + f"{RESET}\n")


def can_bind_port(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_server_responding(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def kill_listening_process(port: int) -> bool:
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid == "0":
                    continue
                print(f"{YELLOW}  Liberando proceso previo (PID {pid}) en puerto {port}...{RESET}")
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=5
                )
                time.sleep(2)
                return True
    except Exception as e:
        print(f"{YELLOW}  Aviso al intentar liberar puerto: {e}{RESET}")
    return False


def open_browser_when_ready(url: str, max_wait: int = 40):
    def _wait_and_open():
        for _ in range(max_wait):
            time.sleep(1)
            if is_server_responding(HOST, PORT):
                time.sleep(1.5)
                print(f"\n{GREEN}  [OK] Servidor listo - abriendo navegador en {url}{RESET}\n")
                webbrowser.open(url)
                return
        print(f"{YELLOW}  Tiempo de espera agotado. Abre el navegador manualmente: {url}{RESET}")

    t = threading.Thread(target=_wait_and_open, daemon=True)
    t.start()


def main():
    os.system("")

    banner()

    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    dev_mode = os.environ.get("DEV_MODE", "0") == "1"

    uvicorn_path = os.path.join(project_dir, "venv", "Scripts", "uvicorn.exe")
    base_exe = [uvicorn_path] if os.path.isfile(uvicorn_path) else [sys.executable, "-m", "uvicorn"]

    cmd = base_exe + [
        "api.main_bots:app",
        "--port", str(PORT),
        "--host", HOST,
        "--ws-max-size", "104857600",
        "--ws-ping-interval", "60",
        "--ws-ping-timeout", "120",
        "--timeout-keep-alive", "120",
    ]

    if dev_mode:
        reload_dirs = ["api", "web_gui", "backtest_engine", "strategy_engine",
                       "data_layer", "ml_engine", "execution_engine", "notifications", "reconciliation"]
        cmd.append("--reload")
        cmd += ["--reload-exclude", "*.pyc", "--reload-exclude", "*.nbi", "--reload-exclude", "*__pycache__*"]
        for rd in reload_dirs:
            rd_path = os.path.join(project_dir, rd)
            if os.path.isdir(rd_path):
                cmd.extend(["--reload-dir", rd_path])
        print(f"{YELLOW}  [DEV_MODE] Hot-reload activado.{RESET}\n")

    print(f"Verificando puerto {PORT}...")
    if not can_bind_port(HOST, PORT):
        print(f"{YELLOW}  Puerto {PORT} ocupado. Intentando liberar...{RESET}")
        kill_listening_process(PORT)
        if not can_bind_port(HOST, PORT):
            print(f"{RED}[ERROR] El puerto {PORT} sigue ocupado.")
            print(f"Abre el Administrador de tareas y cierra cualquier proceso 'python' o 'uvicorn'.{RESET}")
            input("\nPresiona Enter para salir...")
            sys.exit(1)
    print(f"{GREEN}  Puerto {PORT} disponible.{RESET}\n")

    open_browser_when_ready(URL)

    print(f"{GREEN}Iniciando servidor BOTS en {URL} ...{RESET}")
    print(f"{CYAN}Presiona Ctrl+C para detener el servidor.\n{RESET}")

    try:
        subprocess.run(cmd, cwd=project_dir)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print(f"{RED}[ERROR] No se pudo ejecutar uvicorn.{RESET}")
        input("\nPresiona Enter para salir...")
        sys.exit(1)

    print(f"\n{YELLOW}Servidor detenido.{RESET}")
    input("Presiona Enter para cerrar esta ventana...")


if __name__ == "__main__":
    main()
