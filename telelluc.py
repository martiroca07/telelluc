import queue
import threading
import tkinter as tk
from tkinter import messagebox
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 5005
events = queue.Queue()


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/crash":
            events.put("crash")
            self.send_response(200)
        else:
            self.send_response(404)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_server():
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def poll_queue():
    try:
        while True:
            events.get_nowait()
            root.lift()
            root.focus_force()
            messagebox.showerror("Error", "Error")
    except queue.Empty:
        pass
    root.after(100, poll_queue)


root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)

threading.Thread(target=run_server, daemon=True).start()
print(f"Listening on http://127.0.0.1:{PORT} ... (Ctrl+C to stop)", flush=True)

root.after(100, poll_queue)
root.mainloop()
