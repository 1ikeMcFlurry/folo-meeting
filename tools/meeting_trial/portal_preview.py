"""Loopback-only visual preview of the offline setup page, with synthetic networks."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import time

HTML = Path(__file__).resolve().parents[2] / "components/platform/platform_esp32/assets/meeting_wifi.html"


class Preview(BaseHTTPRequestHandler):
    state = {"device": "Folo-Meeting-DEMO", "token": "preview-only", "busy": False,
             "connected": False, "saved": False, "complete": False, "message": ""}
    ready_at = 0
    fail = False

    def log_message(self, *args):
        pass # Never log submitted form contents.

    def reply(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            if Preview.ready_at and time.monotonic() >= Preview.ready_at:
                Preview.state.update(busy=False, connected=not Preview.fail, saved=not Preview.fail,
                                     complete=not Preview.fail,
                                     message="连接失败, 请检查密码和信号" if Preview.fail else "连接成功, Wi-Fi 已保存")
                Preview.ready_at = 0
            self.reply(Preview.state)
        elif self.path == "/api/networks":
            self.reply([{"ssid": "会议室 Wi-Fi", "signal": -45, "secured": True},
                        {"ssid": "Folo Lab 2.4G", "signal": -70, "secured": True},
                        {"ssid": "Lab <test>", "signal": -82, "secured": False}])
        else:
            body = HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/config" or self.headers.get("X-Setup-Token") != "preview-only":
            self.reply({}, 403)
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 1024:
            self.reply({}, 400)
            return
        value = json.loads(self.rfile.read(length))
        Preview.fail = value.get("password") == "wrongpass"
        Preview.state.update(busy=True, complete=False, message="正在验证网络连接")
        Preview.ready_at = time.monotonic() + 2
        self.reply({"accepted": True})


if __name__ == "__main__":
    print("Preview: http://127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Preview).serve_forever()
