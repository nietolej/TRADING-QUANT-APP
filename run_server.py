"""
Script de inicio de Trading Quant App.
Maneja: liberación de puerto, arranque de uvicorn y apertura del navegador.
"""
import subprocess
import sys
import os
import socket
import time
import webbrowser
import threading

# Forzar UTF-8 en stdout para evitar UnicodeEncodeError en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://localhost:{PORT}"

# Colores ANSI para la consola
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def banner():
    print(f"\n{CYAN}{BOLD}" + "=" * 50)
    print("   Trading Quant App - FastAPI + NiceGUI")
    print("=" * 50 + f"{RESET}\n")


def can_bind_port(host: str, port: int) -> bool:
    """Devuelve True si el puerto está LIBRE para hacer bind (como lo haría uvicorn)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_server_responding(host: str, port: int) -> bool:
    """Devuelve True si hay un servidor HTTP activo en ese puerto."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def kill_listening_process(port: int) -> bool:
    """Mata solo el proceso en estado LISTENING en el puerto (no TIME_WAIT)."""
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
    """Espera a que el servidor responda y abre el navegador en background."""
    def _wait_and_open():
        for _ in range(max_wait):
            time.sleep(1)
            if is_server_responding(HOST, PORT):
                time.sleep(1.5)  # pausa extra para que NiceGUI termine de inicializar
                print(f"\n{GREEN}  [OK] Servidor listo - abriendo navegador en {url}{RESET}\n")
                webbrowser.open(url)
                return
        print(f"{YELLOW}  Tiempo de espera agotado. Abre el navegador manualmente: {url}{RESET}")

    t = threading.Thread(target=_wait_and_open, daemon=True)
    t.start()


def main():
    # Habilitar colores ANSI en Windows
    os.system("")

    banner()

    # Directorio raíz = donde está este script
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    # Hot-reload solo se activa si DEV_MODE=1 (para desarrollo de código).
    # En uso normal NO se usa --reload para que el backtest y otras
    # operaciones pesadas nunca causen un reinicio inesperado del servidor.
    dev_mode = os.environ.get("DEV_MODE", "0") == "1"

    # Ruta a uvicorn
    uvicorn_path = os.path.join(project_dir, "venv", "Scripts", "uvicorn.exe")
    base_exe = [uvicorn_path] if os.path.isfile(uvicorn_path) else [sys.executable, "-m", "uvicorn"]

    cmd = base_exe + [
        "api.main:app",
        "--port", str(PORT),
        "--host", HOST,
        "--ws-max-size", "104857600",
        "--ws-ping-interval", "60",
        "--ws-ping-timeout", "120",
        "--timeout-keep-alive", "120",
    ]

    # Activar reload solo en modo desarrollo
    if dev_mode:
        reload_dirs = ["api", "web_gui", "backtest_engine", "strategy_engine",
                       "data_layer", "ml_engine", "execution_engine", "notifications"]
        cmd.append("--reload")
        cmd += ["--reload-exclude", "*.pyc", "--reload-exclude", "*.nbi", "--reload-exclude", "*__pycache__*"]
        for rd in reload_dirs:
            rd_path = os.path.join(project_dir, rd)
            if os.path.isdir(rd_path):
                cmd.extend(["--reload-dir", rd_path])
        print(f"{YELLOW}  [DEV_MODE] Hot-reload activado.{RESET}\n")

    # Verificar y liberar el puerto si está en uso (LISTENING)
    print(f"Verificando puerto {PORT}...")
    if not can_bind_port(HOST, PORT):
        print(f"{YELLOW}  Puerto {PORT} ocupado. Intentando liberar...{RESET}")
        kill_listening_process(PORT)
        # Segunda verificación
        if not can_bind_port(HOST, PORT):
            print(f"{RED}[ERROR] El puerto {PORT} sigue ocupado.")
            print(f"Abre el Administrador de tareas y cierra cualquier proceso 'python' o 'uvicorn'.{RESET}")
            input("\nPresiona Enter para salir...")
            sys.exit(1)
    print(f"{GREEN}  Puerto {PORT} disponible.{RESET}\n")

    # Lanzar browser en background cuando el servidor esté listo
    open_browser_when_ready(URL)

    # Arrancar uvicorn
    print(f"{GREEN}Iniciando servidor en {URL} ...{RESET}")
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
