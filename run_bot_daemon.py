"""
Punto de entrada para el Trading Daemon Core (Puerto 8001).
Ejecuta el motor de trading autónomo 24/7 en un proceso headless independiente.
"""

import os
import sys
import time
import socket
import subprocess

# Forzar UTF-8 en stdout para evitar UnicodeEncodeError en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOST = "127.0.0.1"
PORT = 8001

# Colores ANSI
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
MAGENTA = "\033[95m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def banner():
    print(f"\n{MAGENTA}{BOLD}" + "=" * 56)
    print("   🤖 TRADING DAEMON CORE - 24/7 EXECUTION ENGINE")
    print("      (Proceso Desacoplado - Puerto 8001)")
    print("=" * 56 + f"{RESET}\n")


def can_bind_port(host: str, port: int) -> bool:
    """Verifica si el puerto está libre."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def kill_listening_process(port: int) -> bool:
    """Libera el puerto 8001 si quedó ocupado por un proceso colgado previo."""
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


def main():
    os.system("")  # Habilitar ANSI en Windows
    banner()

    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    print(f"Verificando disponibilidad de puerto {PORT}...")
    if not can_bind_port(HOST, PORT):
        print(f"{YELLOW}  Puerto {PORT} ocupado. Intentando liberar...{RESET}")
        kill_listening_process(PORT)
        if not can_bind_port(HOST, PORT):
            print(f"{RED}[ERROR] El puerto {PORT} sigue ocupado.{RESET}")
            sys.exit(1)
    print(f"{GREEN}  Puerto {PORT} disponible.{RESET}\n")

    uvicorn_path = os.path.join(project_dir, "venv", "Scripts", "uvicorn.exe")
    base_exe = [uvicorn_path] if os.path.isfile(uvicorn_path) else [sys.executable, "-m", "uvicorn"]

    cmd = base_exe + [
        "execution_engine.bot_daemon:app",
        "--host", HOST,
        "--port", str(PORT),
        "--timeout-keep-alive", "120",
        "--log-level", "info"
    ]

    # Auto-reload solo en modo desarrollo (DEV_MODE=1). Al reiniciar, BotManager
    # relee bots_state.json y reanuda automaticamente los bots que estaban activos.
    dev_mode = os.environ.get("DEV_MODE", "0") == "1"
    if dev_mode:
        reload_dirs = ["execution_engine", "strategy_engine", "data_layer", "analytics"]
        cmd.append("--reload")
        cmd += ["--reload-exclude", "*.pyc", "--reload-exclude", "*.nbi", "--reload-exclude", "*__pycache__*"]
        for rd in reload_dirs:
            rd_path = os.path.join(project_dir, rd)
            if os.path.isdir(rd_path):
                cmd.extend(["--reload-dir", rd_path])
        print(f"{YELLOW}  [DEV_MODE] Hot-reload activado. Los bots activos se reanudaran automaticamente tras cada recarga.{RESET}\n")

    print(f"{GREEN}Iniciando Trading Daemon en http://{HOST}:{PORT}{RESET}")
    print(f"{CYAN}Presiona Ctrl+C para detener el daemon.\n{RESET}")

    try:
        subprocess.run(cmd, cwd=project_dir)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print(f"{RED}[ERROR] No se pudo ejecutar uvicorn.{RESET}")
        sys.exit(1)

    print(f"\n{YELLOW}Trading Daemon detenido de forma ordenada.{RESET}")


if __name__ == "__main__":
    main()
